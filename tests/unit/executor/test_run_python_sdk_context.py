from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_type

import pytest
from tracecat_registry import ctx as registry_ctx
from tracecat_registry.context import RegistryContext, clear_context, set_context

from tracecat.auth.types import Role
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox import SandboxService, validate_run_python_script
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import SandboxConfig
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


class _FakeCasesClient:
    async def list_cases(self, *, limit: int) -> dict[str, Any]:
        return {"items": [{"id": "case-1"}], "limit": limit}


class _FakeTracecatClient:
    @property
    def cases(self) -> _FakeCasesClient:
        return _FakeCasesClient()


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


def test_validate_run_python_script_allows_async_main() -> None:
    is_valid, error = validate_run_python_script(
        "async def main():\n    return {'ok': True}"
    )

    assert is_valid
    assert error is None


def test_nsjail_env_map_adds_sdk_pythonpath(tmp_path: Path) -> None:
    sdk_path = tmp_path / "sdk"
    sdk_path.mkdir()
    sandbox_config = SandboxConfig(
        env_vars={"PYTHONPATH": "/work/lib", "CUSTOM_VALUE": "kept"},
        python_path_dir=sdk_path,
    )

    env_map = NsjailExecutor()._build_env_map(sandbox_config, phase="execute")

    assert env_map["PYTHONPATH"] == "/pythonpath:/work/lib"
    assert env_map["CUSTOM_VALUE"] == "kept"


def test_nsjail_config_mounts_sdk_paths_under_pythonpath_root(
    tmp_path: Path,
) -> None:
    sdk_path = tmp_path / "run-python-sdk"
    sdk_path.mkdir()
    sandbox_config = SandboxConfig(python_path_dir=sdk_path)

    config_text = NsjailExecutor(rootfs_path=str(tmp_path))._build_config(
        tmp_path,
        "execute",
        sandbox_config,
    )

    assert (
        f'mount {{ src: "{sdk_path}" dst: "/pythonpath" is_bind: true rw: false }}'
    ) in config_text


def test_nsjail_env_map_prefers_installed_dependencies_over_sdk_mounts(
    tmp_path: Path,
) -> None:
    sdk_path = tmp_path / "sdk"
    sdk_path.mkdir()
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    cache_key = "abc123"
    (executor.package_cache / cache_key / "site-packages").mkdir(parents=True)
    sandbox_config = SandboxConfig(
        env_vars={"PYTHONPATH": "/work/lib"},
        python_path_dir=sdk_path,
    )

    env_map = executor._build_env_map(
        sandbox_config,
        phase="execute",
        cache_key=cache_key,
    )

    assert env_map["PYTHONPATH"] == "/packages:/pythonpath:/work/lib"


def test_sdk_python_path_does_not_include_executor_site_packages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "registry-source"
    package_root = source_root / "tracecat_registry"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("")
    monkeypatch.setattr(
        "tracecat.sandbox.service.config.TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH",
        str(source_root),
    )
    monkeypatch.setattr(
        "tracecat.sandbox.service.config.TRACECAT__EXECUTOR_SITE_PACKAGES_DIR",
        "",
    )

    sdk_python_path = SandboxService(
        cache_dir=str(tmp_path / "cache")
    )._prepare_sdk_python_path()
    purelib = Path(sysconfig.get_path("purelib")).resolve()

    assert sdk_python_path is not None
    assert sdk_python_path.resolve() != purelib
    assert (sdk_python_path / "tracecat_registry" / "__init__.py").exists()
    assert (sdk_python_path / "pydantic").exists()
    assert (sdk_python_path / "typing_extensions.py").exists()
    assert all(child.resolve() != purelib for child in sdk_python_path.iterdir())


def test_sdk_python_path_mounts_can_import_ctx_without_site_packages(
    tmp_path: Path,
) -> None:
    executor = UnsafePidExecutor(cache_dir=str(tmp_path / "cache"))
    sdk_python_path = SandboxService(
        cache_dir=str(tmp_path / "sandbox-cache")
    )._prepare_sdk_python_path()
    env_vars = executor._with_python_path(
        {},
        sdk_python_path,
    )

    assert sdk_python_path is not None
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


def test_unsafe_pid_sdk_pythonpath_uses_prepared_sdk_dir(tmp_path: Path) -> None:
    sdk_path = tmp_path / "run-python-sdk"
    sdk_path.mkdir()
    executor = UnsafePidExecutor(cache_dir=str(tmp_path / "cache"))

    env_vars = executor._with_python_path(
        {"PYTHONPATH": "/work/lib"},
        sdk_path,
    )

    assert env_vars["PYTHONPATH"] == os.pathsep.join([str(sdk_path), "/work/lib"])


def test_unsafe_pid_pythonpath_prefers_installed_dependencies_over_sdk_paths(
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
) -> None:
    captured: dict[str, Any] = {}

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

    resolved_context = _make_run_python_context()
    input_data = _make_run_python_input()
    result = await DirectBackend().execute(
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


@pytest.mark.anyio
async def test_run_python_subprocess_can_import_registry_ctx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(SandboxService, "_is_nsjail_available", lambda self: False)

    script = """
from tracecat_registry import ctx

def main():
    return {
        "workspace_id": ctx.workspace_id,
        "workflow_id": ctx.workflow_id,
        "run_id": ctx.run_id,
        "api_url": ctx.api_url,
        "token": ctx.token,
    }
"""

    result = await SandboxService(cache_dir=str(tmp_path / "sandbox-cache")).run_python(
        script=script,
        env_vars={
            "TRACECAT__API_URL": "http://api.test:8000",
            "TRACECAT__WORKSPACE_ID": "workspace-id",
            "TRACECAT__WORKFLOW_ID": "workflow-id",
            "TRACECAT__RUN_ID": "run-id",
            "TRACECAT__EXECUTOR_TOKEN": "executor-token",
        },
    )

    assert result == {
        "workspace_id": "workspace-id",
        "workflow_id": "workflow-id",
        "run_id": "run-id",
        "api_url": "http://api.test:8000",
        "token": "executor-token",
    }
