from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_type

import pytest
import tracecat_registry
from tracecat_registry import ctx as registry_ctx
from tracecat_registry.context import RegistryContext, clear_context, set_context

import tracecat.executor.backends.base as executor_backend_module
import tracecat.sandbox.service as sandbox_service_module
from tracecat.auth.types import Role
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.action_gateway.config import ACTION_GATEWAY_SANDBOX_SOCKET
from tracecat.executor.action_gateway.server import ActionGateway
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox import (
    SandboxExecutionError,
    SandboxService,
    validate_run_python_script,
)
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import SandboxConfig, SandboxResult
from tracecat.sandbox.unsafe_pid_executor import (
    SAFE_WRAPPER_SCRIPT as UNSAFE_PID_WRAPPER_SCRIPT,
)
from tracecat.sandbox.unsafe_pid_executor import UnsafePidExecutor
from tracecat.sandbox.wrapper import WRAPPER_SCRIPT

if TYPE_CHECKING:
    from tracecat_registry import types as registry_types

    assert_type(
        registry_ctx.cases.list_cases(limit=10),
        registry_types.CaseListResponse,
    )

    async def _check_registry_ctx_aio_types() -> None:
        assert_type(
            await registry_ctx.cases.aio.list_cases(limit=10),
            registry_types.CaseListResponse,
        )

    _ = _check_registry_ctx_aio_types


@pytest.fixture(autouse=True, scope="session")
def default_org() -> None:
    pass


@pytest.fixture(autouse=True, scope="session")
def workflow_bucket() -> None:
    pass


@pytest.fixture(autouse=True)
def clean_redis_db() -> None:
    pass


class _FakeCasesClient:
    async def list_cases(self, *, limit: int) -> dict[str, Any]:
        return {"items": [{"id": "case-1"}], "limit": limit}


class _FakeTracecatClient:
    @property
    def cases(self) -> _FakeCasesClient:
        return _FakeCasesClient()


class _CapturingNsjailExecutor:
    def __init__(self) -> None:
        self.config: SandboxConfig | None = None

    async def execute(
        self,
        _job_dir: Path,
        config: SandboxConfig,
        _cache_key: str | None,
    ) -> SandboxResult:
        self.config = config
        return SandboxResult(success=True, output={"ok": True})


def _run_wrapper_source(
    wrapper_source: str,
    tmp_path: Path,
    script: str,
) -> dict[str, Any]:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "script.py").write_text(script)
    (work_dir / "inputs.json").write_text("{}")

    rewritten_wrapper = wrapper_source.replace('"/work/', f'"{work_dir}/')
    wrapper_path = tmp_path / "wrapper.py"
    wrapper_path.write_text(rewritten_wrapper)

    subprocess.run(
        [sys.executable, str(wrapper_path)],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    return json.loads((work_dir / "result.json").read_text())


def _run_unsafe_pid_wrapper_source(
    tmp_path: Path,
    script: str,
) -> dict[str, Any]:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "script.py").write_text(script)
    (work_dir / "inputs.json").write_text("{}")

    wrapper_path = tmp_path / "wrapper.py"
    wrapper_path.write_text(UNSAFE_PID_WRAPPER_SCRIPT.format(work_dir=str(work_dir)))

    subprocess.run(
        [sys.executable, str(wrapper_path)],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    return json.loads((work_dir / "result.json").read_text())


def _make_run_python_input() -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    exec_id = ExecutionUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            action=PlatformAction.RUN_PYTHON,
            args={},
            ref="run_python",
        ),
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={PlatformAction.RUN_PYTHON: "tracecat_registry"},
        ),
    )


def _make_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.UUID("38be3315-c172-4332-aea6-53fc4b93f053"),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _make_run_python_context() -> ResolvedContext:
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="udf",
            action_name=PlatformAction.RUN_PYTHON,
            module="tracecat_registry.core.python",
            name="run_python",
            origin="tracecat_registry",
        ),
        evaluated_args={
            "script": "def main():\n    return 1",
            "env_vars": {
                "CUSTOM_VALUE": "kept",
                "TRACECAT__WORKSPACE_ID": "user-cannot-shadow-context",
            },
        },
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        executor_token="executor-token",
        logical_time=datetime.now(UTC),
    )


def _run_python_nsjail_available() -> bool:
    nsjail_path = Path(sandbox_service_module.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(sandbox_service_module.TRACECAT__SANDBOX_ROOTFS_PATH)
    return (
        platform.system() == "Linux"
        and nsjail_path.is_file()
        and os.access(nsjail_path, os.X_OK)
        and rootfs_path.is_dir()
    )


def _docker_nsjail_fallback_enabled() -> bool:
    return (
        os.environ.get("TRACECAT__RUN_PYTHON_NSJAIL_DOCKER_FALLBACK_CHILD") != "1"
        and shutil.which("docker") is not None
    )


def _set_run_python_nsjail_mode(
    monkeypatch: pytest.MonkeyPatch,
    *,
    disable_nsjail: bool,
) -> None:
    monkeypatch.setattr(
        sandbox_service_module,
        "TRACECAT__DISABLE_NSJAIL",
        disable_nsjail,
    )


def _registry_ctx_smoke_script() -> str:
    return """
from tracecat_registry import ctx

def main():
    return {
        "workspace_id": ctx.workspace_id,
        "workflow_id": ctx.workflow_id,
        "run_id": ctx.run_id,
        "wf_exec_id": ctx.wf_exec_id,
        "environment": ctx.environment,
        "api_url": ctx.api_url,
        "token": ctx.token,
        "has_sync_list_cases": callable(ctx.cases.list_cases),
        "has_async_list_cases": callable(ctx.cases.aio.list_cases),
        "has_modal_style_method_aio": hasattr(ctx.cases.list_cases, "aio"),
    }
"""


def _registry_ctx_gateway_smoke_script() -> str:
    return """
from tracecat_registry import ctx


async def main():
    import os
    import urllib.request

    try:
        urllib.request.urlopen("https://example.com", timeout=2)
        outbound_status = "allowed"
    except Exception as exc:
        outbound_status = f"blocked: {type(exc).__name__}"

    return {
        "gateway": await ctx.client.get("/health"),
        "socket_path": os.environ.get("TRACECAT__ACTION_GATEWAY_SOCKET"),
        "outbound_status": outbound_status,
    }
"""


def _registry_ctx_env_vars() -> dict[str, str]:
    return {
        "TRACECAT__API_URL": "http://api.test:8000",
        "TRACECAT__WORKSPACE_ID": "workspace-id",
        "TRACECAT__WORKFLOW_ID": "workflow-id",
        "TRACECAT__RUN_ID": "run-id",
        "TRACECAT__WF_EXEC_ID": "workflow-id/execution-id",
        "TRACECAT__ENVIRONMENT": "testing",
        "TRACECAT__EXECUTOR_TOKEN": "executor-token",
    }


def _expected_registry_ctx_smoke_result(
    *,
    workspace_id: str = "workspace-id",
    workflow_id: str = "workflow-id",
    run_id: str = "run-id",
    wf_exec_id: str = "workflow-id/execution-id",
    environment: str = "testing",
    api_url: str = "http://api.test:8000",
    token: str = "executor-token",
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "wf_exec_id": wf_exec_id,
        "environment": environment,
        "api_url": api_url,
        "token": token,
        "has_sync_list_cases": True,
        "has_async_list_cases": True,
        "has_modal_style_method_aio": False,
    }


def _registry_python_path_dirs() -> list[Path]:
    registry_source_root = Path(tracecat_registry.__file__).resolve().parents[1]
    purelib = Path(sysconfig.get_path("purelib")).resolve()
    return [registry_source_root, purelib]


def _write_legacy_registry_sdk(root: Path) -> Path:
    """Write a minimal pre-gateway tracecat_registry package for nsjail tests."""
    package_dir = root / "tracecat_registry"
    sdk_dir = package_dir / "sdk"
    sdk_dir.mkdir(parents=True)

    (package_dir / "__init__.py").write_text("from . import ctx\n")
    (sdk_dir / "__init__.py").write_text("")
    (package_dir / "ctx.py").write_text(
        """
from tracecat_registry.context import get_context


def __getattr__(name):
    if name == "client":
        return get_context().client
    raise AttributeError(name)
"""
    )
    (package_dir / "context.py").write_text(
        """
import os


_context = None


class RegistryContext:
    def __init__(self, *, api_url, token, workspace_id):
        self.api_url = api_url
        self.token = token
        self.workspace_id = workspace_id

    @classmethod
    def from_env(cls):
        return cls(
            workspace_id=os.environ["TRACECAT__WORKSPACE_ID"],
            api_url=os.environ.get("TRACECAT__API_URL", "http://api:8000"),
            token=os.environ.get("TRACECAT__EXECUTOR_TOKEN", ""),
        )

    @property
    def client(self):
        from tracecat_registry.sdk.client import TracecatClient

        return TracecatClient(
            api_url=self.api_url,
            token=self.token,
            workspace_id=self.workspace_id,
        )


def get_context():
    if _context is None:
        raise RuntimeError("No registry context is set")
    return _context


def set_context(ctx):
    global _context
    _context = ctx


def init_context_from_env():
    ctx = RegistryContext.from_env()
    set_context(ctx)
    return ctx
"""
    )
    (sdk_dir / "client.py").write_text(
        """
import os

import httpx


class TracecatClient:
    def __init__(
        self,
        *,
        api_url=None,
        token=None,
        workspace_id=None,
        timeout=120.0,
    ):
        self._api_url = (api_url or os.environ.get("TRACECAT__API_URL", "http://api:8000")).rstrip("/") + "/internal"
        self._token = token or os.environ.get("TRACECAT__EXECUTOR_TOKEN", "")
        self._timeout = timeout

    def _get_headers(self):
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _handle_error_response(self, response):
        response.raise_for_status()

    async def request(self, method, path, *, params=None, json=None, headers=None):
        request_headers = self._get_headers()
        if headers:
            request_headers.update(headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                f"{self._api_url}{path}",
                params=params,
                json=json,
                headers=request_headers,
            )
        if not response.is_success:
            self._handle_error_response(response)
        if not response.content:
            return None
        return response.json()

    async def get(self, path, *, params=None, headers=None):
        return await self.request("GET", path, params=params, headers=headers)
"""
    )
    return root


async def _run_sandbox_registry_ctx_smoke(
    *,
    cache_dir: Path,
    timeout_seconds: int = 60,
) -> Any:
    return await SandboxService(cache_dir=str(cache_dir)).run_python(
        script=_registry_ctx_smoke_script(),
        env_vars=_registry_ctx_env_vars(),
        python_path_dirs=_registry_python_path_dirs(),
        timeout_seconds=timeout_seconds,
    )


class _FakeRunPythonRegistryPathRunner:
    def __init__(self, paths: list[Path]) -> None:
        self.paths = paths
        self.artifact_uris: list[str] | None = None

    async def resolve_registry_paths(
        self, artifact_uris: list[str] | None = None
    ) -> list[Path]:
        self.artifact_uris = artifact_uris
        return self.paths


async def _run_backend_registry_ctx_smoke(
    *,
    backend: ExecutorBackend,
    monkeypatch: pytest.MonkeyPatch,
    cache_dir: Path,
) -> dict[str, Any]:
    fake_runner = _FakeRunPythonRegistryPathRunner(_registry_python_path_dirs())

    async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
        return ["s3://tracecat-registry/test/site-packages.tar.gz"]

    monkeypatch.setattr(
        executor_backend_module,
        "SandboxService",
        lambda: SandboxService(cache_dir=str(cache_dir)),
    )
    monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)
    monkeypatch.setattr(
        executor_backend_module,
        "get_action_runner",
        lambda: fake_runner,
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__API_URL",
        "http://api.test:8000",
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__ACTION_GATEWAY_ENABLED",
        False,
    )

    input_data = _make_run_python_input()
    resolved_context = _make_run_python_context()
    resolved_context.evaluated_args.update(
        {
            "script": _registry_ctx_smoke_script(),
            "timeout_seconds": 60,
        }
    )

    result = await backend.execute(
        input=input_data,
        role=_make_role(),
        resolved_context=resolved_context,
    )

    assert result.type == "success"
    assert result.result == _expected_registry_ctx_smoke_result(
        workspace_id=resolved_context.workspace_id,
        workflow_id=resolved_context.workflow_id,
        run_id=resolved_context.run_id,
        wf_exec_id=str(input_data.run_context.wf_exec_id),
        environment=input_data.run_context.environment,
        token=resolved_context.executor_token,
    )
    assert fake_runner.artifact_uris == [
        "s3://tracecat-registry/test/site-packages.tar.gz"
    ]
    return result.result


async def _run_backend_registry_ctx_gateway_smoke(
    *,
    backend: ExecutorBackend,
    monkeypatch: pytest.MonkeyPatch,
    cache_dir: Path,
    action_gateway_socket: Path,
    registry_paths: list[Path],
) -> dict[str, Any]:
    """Run a real SDK request through run_python's nsjail Action Gateway mount."""
    fake_runner = _FakeRunPythonRegistryPathRunner(registry_paths)

    async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
        return ["s3://tracecat-registry/test/site-packages.tar.gz"]

    monkeypatch.setattr(
        executor_backend_module,
        "SandboxService",
        lambda: SandboxService(cache_dir=str(cache_dir)),
    )
    monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)
    monkeypatch.setattr(
        executor_backend_module,
        "get_action_runner",
        lambda: fake_runner,
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__API_URL",
        "http://tracecat-api:8000",
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__ACTION_GATEWAY_ENABLED",
        True,
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__ACTION_GATEWAY_SOCKET",
        str(action_gateway_socket),
    )

    input_data = _make_run_python_input()
    resolved_context = _make_run_python_context()
    resolved_context.evaluated_args.update(
        {
            "script": _registry_ctx_gateway_smoke_script(),
            "allow_network": False,
            "timeout_seconds": 60,
        }
    )

    result = await backend.execute(
        input=input_data,
        role=_make_role(),
        resolved_context=resolved_context,
    )

    assert result.type == "success"
    assert result.result == {
        "gateway": {"status": "ok"},
        "socket_path": str(ACTION_GATEWAY_SANDBOX_SOCKET),
        "outbound_status": "blocked: URLError",
    }
    assert fake_runner.artifact_uris == [
        "s3://tracecat-registry/test/site-packages.tar.gz"
    ]
    return result.result


async def _run_backend_registry_ctx_smoke_matrix(
    *,
    monkeypatch: pytest.MonkeyPatch,
    cache_dir: Path,
) -> None:
    for backend in (DirectBackend(), EphemeralBackend()):
        result = await _run_backend_registry_ctx_smoke(
            backend=backend,
            monkeypatch=monkeypatch,
            cache_dir=cache_dir / backend.__class__.__name__,
        )
        assert isinstance(result, dict)


async def _run_backend_registry_ctx_gateway_smoke_matrix(
    *,
    monkeypatch: pytest.MonkeyPatch,
    cache_dir: Path,
) -> None:
    action_gateway_socket = cache_dir / "action-gateway.sock"
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__ACTION_GATEWAY_ENABLED",
        True,
    )
    action_gateway = ActionGateway(socket_path=action_gateway_socket)
    await action_gateway.start()
    legacy_registry_root = _write_legacy_registry_sdk(cache_dir / "legacy-registry")

    try:
        registry_path_cases = [
            _registry_python_path_dirs(),
            [legacy_registry_root, Path(sysconfig.get_path("purelib")).resolve()],
        ]
        for index, registry_paths in enumerate(registry_path_cases):
            result = await _run_backend_registry_ctx_gateway_smoke(
                backend=DirectBackend(),
                monkeypatch=monkeypatch,
                cache_dir=cache_dir / f"sandbox-cache-{index}",
                action_gateway_socket=action_gateway_socket,
                registry_paths=registry_paths,
            )
            assert isinstance(result, dict)
    finally:
        await action_gateway.stop()


def _run_nsjail_harness_in_docker_or_skip(
    *,
    cli_arg: str,
    override_prefix: str,
    timeout: int,
    failure_message: str,
) -> None:
    if os.environ.get("TRACECAT__RUN_PYTHON_NSJAIL_DOCKER_FALLBACK_CHILD") == "1":
        pytest.skip("run_python nsjail unavailable inside Docker fallback child")
    if not _docker_nsjail_fallback_enabled():
        pytest.skip("Docker CLI unavailable for run_python nsjail fallback")

    docker_info = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if docker_info.returncode != 0:
        pytest.skip("Docker daemon unavailable for run_python nsjail fallback")

    repo_root = Path(__file__).resolve().parents[3]
    compose_env = os.environ.copy()
    compose_env.setdefault(
        "TRACECAT__LOCAL_REPOSITORY_PATH",
        str(repo_root / "packages"),
    )
    compose_env.setdefault("TRACECAT__LOCAL_REPOSITORY_ENABLED", "false")
    compose_env.setdefault("PUBLIC_APP_PORT", "80")
    compose_env.setdefault("BASE_DOMAIN", ":80")
    compose_env.setdefault("ADDRESS", "0.0.0.0")
    compose_env.setdefault("LOG_LEVEL", "INFO")
    compose_env.setdefault("TRACECAT__APP_ENV", "development")
    tests_mount = f"{repo_root / 'tests'}:/app/tests:ro"
    fd, override_name = tempfile.mkstemp(
        prefix=override_prefix,
        suffix=".yml",
    )
    os.close(fd)
    override_path = Path(override_name)
    override_path.write_text(
        "\n".join(
            [
                "services:",
                "  api:",
                "    build:",
                "      target: test",
                "    cap_add:",
                "      - SYS_ADMIN",
                "    security_opt:",
                "      - seccomp:unconfined",
                "      - systempaths=unconfined",
                "    volumes:",
                f"      - {json.dumps(tests_mount)}",
                "    environment:",
                '      TRACECAT__RUN_PYTHON_NSJAIL_DOCKER_FALLBACK_CHILD: "1"',
                '      TRACECAT__DISABLE_NSJAIL: "false"',
                '      TRACECAT__SANDBOX_NSJAIL_PATH: "/usr/local/bin/nsjail"',
                '      TRACECAT__SANDBOX_ROOTFS_PATH: "/var/lib/tracecat/sandbox-rootfs"',
                '      PYTHONDONTWRITEBYTECODE: "1"',
                "",
            ]
        )
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(repo_root / "docker-compose.dev.yml"),
                "-f",
                str(override_path),
                "run",
                "--rm",
                "--no-deps",
                "--build",
                "-T",
                "--entrypoint",
                "sh",
                "api",
                "-lc",
                "uv run python -m tests.unit.executor.test_run_python_sdk_context "
                f"{cli_arg}",
            ],
            cwd=repo_root,
            env=compose_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        override_path.unlink(missing_ok=True)

    if result.returncode != 0:
        pytest.fail(
            f"{failure_message}\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def _run_nsjail_sdk_context_harness_in_docker_or_skip() -> None:
    _run_nsjail_harness_in_docker_or_skip(
        cli_arg="--run-nsjail-sdk-context-smoke",
        override_prefix="tracecat-run-python-nsjail-test-",
        timeout=180,
        failure_message="Dockerized run_python nsjail SDK-context fallback failed.",
    )


def _run_nsjail_sdk_gateway_harness_in_docker_or_skip() -> None:
    _run_nsjail_harness_in_docker_or_skip(
        cli_arg="--run-nsjail-sdk-gateway-smoke",
        override_prefix="tracecat-run-python-nsjail-gateway-test-",
        timeout=240,
        failure_message="Dockerized run_python nsjail SDK-gateway fallback failed.",
    )


def _run_nsjail_sdk_context_smoke_from_cli() -> None:
    async def run() -> None:
        monkeypatch = pytest.MonkeyPatch()
        tmp_path = Path(tempfile.mkdtemp(prefix="tracecat-run-python-nsjail-"))
        try:
            _set_run_python_nsjail_mode(monkeypatch, disable_nsjail=False)
            await _run_backend_registry_ctx_smoke_matrix(
                monkeypatch=monkeypatch,
                cache_dir=tmp_path / "sandbox-cache",
            )
        finally:
            monkeypatch.undo()
            shutil.rmtree(tmp_path, ignore_errors=True)

    asyncio.run(run())


def _run_nsjail_sdk_gateway_smoke_from_cli() -> None:
    async def run() -> None:
        monkeypatch = pytest.MonkeyPatch()
        tmp_path = Path(tempfile.mkdtemp(prefix="tracecat-run-python-gateway-"))
        try:
            _set_run_python_nsjail_mode(monkeypatch, disable_nsjail=False)
            await _run_backend_registry_ctx_gateway_smoke_matrix(
                monkeypatch=monkeypatch,
                cache_dir=tmp_path,
            )
        finally:
            monkeypatch.undo()
            shutil.rmtree(tmp_path, ignore_errors=True)

    asyncio.run(run())


def test_validate_run_python_script_allows_async_main() -> None:
    is_valid, error = validate_run_python_script(
        "async def main():\n    return {'ok': True}"
    )

    assert is_valid
    assert error is None


def test_nsjail_env_map_adds_registry_pythonpaths(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry"
    dependency_path = tmp_path / "dependencies"
    registry_path.mkdir()
    dependency_path.mkdir()
    sandbox_config = SandboxConfig(
        env_vars={"PYTHONPATH": "/work/lib", "CUSTOM_VALUE": "kept"},
        python_path_dirs=[registry_path, dependency_path],
    )

    env_map = NsjailExecutor()._build_env_map(sandbox_config, phase="execute")

    assert env_map["PYTHONPATH"] == "/pythonpath/0:/pythonpath/1:/work/lib"
    assert env_map["CUSTOM_VALUE"] == "kept"


def test_nsjail_config_mounts_registry_paths_under_pythonpath_root(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry"
    dependency_path = tmp_path / "dependencies"
    registry_path.mkdir()
    dependency_path.mkdir()
    sandbox_config = SandboxConfig(python_path_dirs=[registry_path, dependency_path])

    config_text = NsjailExecutor(rootfs_path=str(tmp_path))._build_config(
        tmp_path,
        "execute",
        sandbox_config,
    )

    assert (
        f'mount {{ src: "{registry_path}" dst: "/pythonpath/0" is_bind: true rw: false }}'
    ) in config_text
    assert (
        f'mount {{ src: "{dependency_path}" dst: "/pythonpath/1" is_bind: true rw: false }}'
    ) in config_text


def test_nsjail_config_mounts_run_python_action_gateway_socket(
    tmp_path: Path,
) -> None:
    action_gateway_socket = tmp_path / "action-gateway.sock"
    action_gateway_socket.touch()
    sandbox_config = SandboxConfig(
        action_gateway_socket=action_gateway_socket,
    )

    config_text = NsjailExecutor(rootfs_path=str(tmp_path))._build_config(
        tmp_path,
        "execute",
        sandbox_config,
    )

    assert (
        f'mount {{ src: "{action_gateway_socket}" '
        f'dst: "{ACTION_GATEWAY_SANDBOX_SOCKET}" is_bind: true rw: false }}'
    ) in config_text


def test_run_python_action_gateway_env_rejects_missing_socket(tmp_path: Path) -> None:
    env_vars = {"CUSTOM_VALUE": "kept"}

    with pytest.raises(SandboxExecutionError, match="socket is unavailable"):
        SandboxService._with_action_gateway_socket_env(
            env_vars,
            socket_path=tmp_path / "missing-action-gateway.sock",
        )


@pytest.mark.anyio
async def test_run_python_nsjail_rejects_missing_action_gateway_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = SandboxService(cache_dir=str(tmp_path / "sandbox-cache"))
    nsjail_executor = _CapturingNsjailExecutor()
    monkeypatch.setattr(service, "_nsjail_executor", nsjail_executor)
    monkeypatch.setattr(service, "_is_nsjail_available", lambda: True)

    with pytest.raises(SandboxExecutionError, match="socket is unavailable"):
        await service.run_python(
            script="def main():\n    return 1",
            env_vars={"CUSTOM_VALUE": "kept"},
            action_gateway_socket=tmp_path / "missing-action-gateway.sock",
        )
    assert nsjail_executor.config is None


def test_nsjail_env_map_prefers_installed_dependencies_over_registry_mounts(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry"
    registry_path.mkdir()
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    cache_key = "abc123"
    (executor.package_cache / cache_key / "site-packages").mkdir(parents=True)
    sandbox_config = SandboxConfig(
        env_vars={"PYTHONPATH": "/work/lib"},
        python_path_dirs=[registry_path],
    )

    env_map = executor._build_env_map(
        sandbox_config,
        phase="execute",
        cache_key=cache_key,
    )

    assert env_map["PYTHONPATH"] == "/packages:/pythonpath/0:/work/lib"


def test_registry_pythonpaths_can_import_ctx_without_site_packages(
    tmp_path: Path,
) -> None:
    executor = UnsafePidExecutor(cache_dir=str(tmp_path / "cache"))
    env_vars = executor._with_python_paths(
        {},
        _registry_python_path_dirs(),
    )

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "from tracecat_registry import ctx; "
            "from tracecat_registry.context import RegistryContext; "
            "print(ctx.__name__, type(RegistryContext("
            "workspace_id='w', workflow_id='wf', run_id='r').client).__name__)",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": env_vars["PYTHONPATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "tracecat_registry.ctx TracecatClient"


def test_unsafe_pid_pythonpath_uses_registry_paths(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry"
    dependency_path = tmp_path / "dependencies"
    registry_path.mkdir()
    dependency_path.mkdir()
    executor = UnsafePidExecutor(cache_dir=str(tmp_path / "cache"))

    env_vars = executor._with_python_paths(
        {"PYTHONPATH": "/work/lib"},
        [registry_path, dependency_path],
    )

    assert env_vars["PYTHONPATH"] == os.pathsep.join(
        [str(registry_path), str(dependency_path), "/work/lib"]
    )


def test_unsafe_pid_pythonpath_prefers_installed_dependencies_over_registry_paths(
    tmp_path: Path,
) -> None:
    executor = UnsafePidExecutor(cache_dir=str(tmp_path / "cache"))
    cached_venv = tmp_path / "venv"
    site_packages = cached_venv / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)

    env_vars = executor._with_venv_site_packages_pythonpath(
        {"PYTHONPATH": os.pathsep.join(["/sdk", "/host/site-packages"])},
        cached_venv,
    )

    assert env_vars["PYTHONPATH"] == os.pathsep.join(
        [str(site_packages), "/sdk", "/host/site-packages"]
    )


def test_nsjail_wrapper_resolves_non_coroutine_awaitable(tmp_path: Path) -> None:
    result = _run_wrapper_source(
        WRAPPER_SCRIPT,
        tmp_path,
        """
class ValueAwaitable:
    def __await__(self):
        async def resolve():
            return {"ok": True}
        return resolve().__await__()

def main():
    return ValueAwaitable()
""",
    )

    assert result["success"] is True
    assert result["output"] == {"ok": True}


def test_unsafe_pid_wrapper_resolves_non_coroutine_awaitable(
    tmp_path: Path,
) -> None:
    result = _run_unsafe_pid_wrapper_source(
        tmp_path,
        """
class ValueAwaitable:
    def __await__(self):
        async def resolve():
            return {"ok": True}
        return resolve().__await__()

def main():
    return ValueAwaitable()
""",
    )

    assert result["success"] is True
    assert result["output"] == {"ok": True}


def test_tracecat_registry_ctx_runs_async_sdk_methods_synchronously() -> None:
    context = RegistryContext(
        workspace_id="workspace-id",
        workflow_id="workflow-id",
        run_id="run-id",
        token="executor-token",
    )
    context.__dict__["client"] = _FakeTracecatClient()
    set_context(context)

    try:
        assert registry_ctx.workspace_id == "workspace-id"
        assert registry_ctx.cases.list_cases(limit=10) == {
            "items": [{"id": "case-1"}],
            "limit": 10,
        }
    finally:
        clear_context()


@pytest.mark.anyio
async def test_tracecat_registry_ctx_supports_typed_async_client_namespace() -> None:
    context = RegistryContext(
        workspace_id="workspace-id",
        workflow_id="workflow-id",
        run_id="run-id",
        token="executor-token",
    )
    context.__dict__["client"] = _FakeTracecatClient()
    set_context(context)

    try:
        assert await registry_ctx.cases.aio.list_cases(limit=7) == {
            "items": [{"id": "case-1"}],
            "limit": 7,
        }
    finally:
        clear_context()


def test_tracecat_registry_ctx_does_not_expose_modal_style_method_aio() -> None:
    context = RegistryContext(
        workspace_id="workspace-id",
        workflow_id="workflow-id",
        run_id="run-id",
        token="executor-token",
    )
    context.__dict__["client"] = _FakeTracecatClient()
    set_context(context)

    try:
        assert not hasattr(registry_ctx.cases.list_cases, "aio")
    finally:
        clear_context()


@pytest.mark.anyio
async def test_tracecat_registry_ctx_rejects_sync_call_in_async_code() -> None:
    context = RegistryContext(
        workspace_id="workspace-id",
        workflow_id="workflow-id",
        run_id="run-id",
        token="executor-token",
    )
    context.__dict__["client"] = _FakeTracecatClient()
    set_context(context)

    try:
        with pytest.raises(RuntimeError, match=r"\.aio"):
            registry_ctx.cases.list_cases(limit=7)
    finally:
        clear_context()


@pytest.mark.anyio
async def test_run_python_backend_always_injects_sdk_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    artifact_path = tmp_path / "registry-artifact"
    artifact_path.mkdir()
    fake_runner = _FakeRunPythonRegistryPathRunner([artifact_path])

    async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
        return ["s3://tracecat-registry/test/site-packages.tar.gz"]

    class FakeSandboxService:
        async def run_python(self, **kwargs: Any) -> dict[str, bool]:
            captured.update(kwargs)
            return {"ok": True}

    monkeypatch.setattr(
        "tracecat.executor.backends.base.SandboxService",
        FakeSandboxService,
    )
    monkeypatch.setattr(
        "tracecat.executor.backends.base.config.TRACECAT__API_URL",
        "http://api.test:8000",
    )
    backend = DirectBackend()
    monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)
    monkeypatch.setattr(
        "tracecat.executor.backends.base.get_action_runner",
        lambda: fake_runner,
    )

    resolved_context = _make_run_python_context()
    input_data = _make_run_python_input()
    result = await backend.execute(
        input=input_data,
        role=_make_role(),
        resolved_context=resolved_context,
    )

    assert result.type == "success"
    assert result.result == {"ok": True}
    assert captured["env_vars"] == {
        "CUSTOM_VALUE": "kept",
        "TRACECAT__API_URL": "http://api.test:8000",
        "TRACECAT__WORKSPACE_ID": resolved_context.workspace_id,
        "TRACECAT__WORKFLOW_ID": resolved_context.workflow_id,
        "TRACECAT__RUN_ID": resolved_context.run_id,
        "TRACECAT__WF_EXEC_ID": str(input_data.run_context.wf_exec_id),
        "TRACECAT__ENVIRONMENT": input_data.run_context.environment,
        "TRACECAT__EXECUTOR_TOKEN": resolved_context.executor_token,
    }
    assert captured["python_path_dirs"] == [artifact_path]
    assert fake_runner.artifact_uris == [
        "s3://tracecat-registry/test/site-packages.tar.gz"
    ]


@pytest.mark.anyio
async def test_run_python_backend_fails_without_registry_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
        return []

    monkeypatch.setattr(
        "tracecat.executor.backends.base.config.TRACECAT__LOCAL_REPOSITORY_ENABLED",
        False,
    )
    backend = DirectBackend()
    monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)

    result = await backend.execute(
        input=_make_run_python_input(),
        role=_make_role(),
        resolved_context=_make_run_python_context(),
    )

    assert result.type == "failure"
    assert result.error.type == "RegistryError"


@pytest.mark.anyio
async def test_run_python_backend_uses_local_registry_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    builtin_path = tmp_path / "builtin-registry"
    local_path = tmp_path / "local-registry"
    custom_target = tmp_path / "custom-target"
    for path in (builtin_path, local_path, custom_target):
        path.mkdir()
    host_site_packages = {
        Path(site_path).resolve()
        for site_path in (
            sysconfig.get_path("purelib"),
            sysconfig.get_path("platlib"),
        )
        if site_path
    }

    async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
        return []

    class FakeSandboxService:
        async def run_python(self, **kwargs: Any) -> dict[str, bool]:
            captured.update(kwargs)
            return {"ok": True}

    class FailingRegistryPathRunner:
        async def resolve_registry_paths(
            self, artifact_uris: list[str] | None = None
        ) -> list[Path]:
            raise AssertionError(
                f"local repository mode should not resolve {artifact_uris=}"
            )

    monkeypatch.setattr(executor_backend_module, "SandboxService", FakeSandboxService)
    monkeypatch.setattr(
        executor_backend_module,
        "get_action_runner",
        lambda: FailingRegistryPathRunner(),
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__LOCAL_REPOSITORY_ENABLED",
        True,
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH",
        str(builtin_path),
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH",
        str(local_path),
    )
    monkeypatch.setenv("PYTHONUSERBASE", str(custom_target))

    backend = DirectBackend()
    monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)

    result = await backend.execute(
        input=_make_run_python_input(),
        role=_make_role(),
        resolved_context=_make_run_python_context(),
    )

    assert result.type == "success"
    assert result.result == {"ok": True}
    assert captured["python_path_dirs"] == [
        builtin_path,
        local_path,
        custom_target,
    ]
    assert (
        not {path.resolve() for path in captured["python_path_dirs"]}
        & host_site_packages
    )


@pytest.mark.anyio
async def test_run_python_subprocess_can_import_registry_ctx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(SandboxService, "_is_nsjail_available", lambda self: False)

    result = await _run_sandbox_registry_ctx_smoke(
        cache_dir=tmp_path / "sandbox-cache",
    )

    assert result == _expected_registry_ctx_smoke_result()


@pytest.mark.anyio
async def test_run_python_backends_can_import_registry_ctx_in_pid_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_run_python_nsjail_mode(monkeypatch, disable_nsjail=True)

    await _run_backend_registry_ctx_smoke_matrix(
        monkeypatch=monkeypatch,
        cache_dir=tmp_path / "sandbox-cache",
    )


@pytest.mark.anyio
async def test_run_python_nsjail_can_import_registry_ctx_with_registry_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if not _run_python_nsjail_available():
        _run_nsjail_sdk_context_harness_in_docker_or_skip()
        return

    _set_run_python_nsjail_mode(monkeypatch, disable_nsjail=False)
    await _run_backend_registry_ctx_smoke_matrix(
        monkeypatch=monkeypatch,
        cache_dir=tmp_path / "sandbox-cache",
    )


@pytest.mark.anyio
async def test_run_python_nsjail_sdk_calls_use_action_gateway_without_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SDK calls should use the internal gateway while outbound network is off.

    This mirrors cloud `core.script.run_python` execution: user egress remains
    disabled by default, but `tracecat_registry.ctx` calls are internal platform
    calls and should route through the worker-owned Unix socket. The matrix also
    uses a minimal pre-gateway registry package to catch cached legacy artifacts
    whose `TracecatClient` does not natively read the gateway socket env var.
    """
    if not _run_python_nsjail_available():
        _run_nsjail_sdk_gateway_harness_in_docker_or_skip()
        return

    _set_run_python_nsjail_mode(monkeypatch, disable_nsjail=False)
    await _run_backend_registry_ctx_gateway_smoke_matrix(
        monkeypatch=monkeypatch,
        cache_dir=tmp_path,
    )


@pytest.mark.anyio
async def test_run_python_pid_sdk_calls_use_action_gateway_with_legacy_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unsafe PID fallback should patch pre-gateway SDK artifacts too."""
    _set_run_python_nsjail_mode(monkeypatch, disable_nsjail=True)
    action_gateway_socket = (
        Path("/tmp") / f"tracecat-run-python-gateway-{uuid.uuid4().hex}.sock"
    )
    action_gateway_socket.unlink(missing_ok=True)
    action_gateway = ActionGateway(socket_path=action_gateway_socket)
    legacy_registry_root = _write_legacy_registry_sdk(tmp_path / "legacy-registry")
    fake_runner = _FakeRunPythonRegistryPathRunner(
        [legacy_registry_root, Path(sysconfig.get_path("purelib")).resolve()]
    )

    async def _get_artifact_uris(_input: RunActionInput, _role: Role) -> list[str]:
        return ["s3://tracecat-registry/test/site-packages.tar.gz"]

    monkeypatch.setattr(
        executor_backend_module,
        "SandboxService",
        lambda: SandboxService(cache_dir=str(tmp_path / "sandbox-cache")),
    )
    monkeypatch.setattr(
        executor_backend_module,
        "get_action_runner",
        lambda: fake_runner,
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__API_URL",
        "http://tracecat-api:8000",
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__ACTION_GATEWAY_ENABLED",
        True,
    )
    monkeypatch.setattr(
        executor_backend_module.config,
        "TRACECAT__ACTION_GATEWAY_SOCKET",
        str(action_gateway_socket),
    )

    backend = DirectBackend()
    monkeypatch.setattr(backend, "_get_artifact_uris", _get_artifact_uris)

    input_data = _make_run_python_input()
    resolved_context = _make_run_python_context()
    resolved_context.evaluated_args.update(
        {
            "script": """
from tracecat_registry import ctx


async def main():
    import os

    return {
        "gateway": await ctx.client.get("/health"),
        "socket_path": os.environ.get("TRACECAT__ACTION_GATEWAY_SOCKET"),
    }
""",
            "allow_network": False,
            "timeout_seconds": 60,
        }
    )

    try:
        await action_gateway.start()
        result = await backend.execute(
            input=input_data,
            role=_make_role(),
            resolved_context=resolved_context,
        )
    finally:
        await action_gateway.stop()
        action_gateway_socket.unlink(missing_ok=True)

    assert result.type == "success", result.error
    assert result.result == {
        "gateway": {"status": "ok"},
        "socket_path": str(action_gateway_socket),
    }
    assert fake_runner.artifact_uris == [
        "s3://tracecat-registry/test/site-packages.tar.gz"
    ]


if __name__ == "__main__":
    if sys.argv[1:] == ["--run-nsjail-sdk-context-smoke"]:
        _run_nsjail_sdk_context_smoke_from_cli()
    elif sys.argv[1:] == ["--run-nsjail-sdk-gateway-smoke"]:
        _run_nsjail_sdk_gateway_smoke_from_cli()
    else:
        raise SystemExit(
            "Usage: python -m tests.unit.executor.test_run_python_sdk_context "
            "[--run-nsjail-sdk-context-smoke|--run-nsjail-sdk-gateway-smoke]"
        )
