"""Docker-backed smoke tests for regular executor nsjail execution."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest
import tracecat_registry

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.action_gateway.config import ACTION_GATEWAY_SANDBOX_SOCKET
from tracecat.executor.action_gateway.server import ActionGateway
from tracecat.executor.action_runner import ActionRunner
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.registry_artifacts import (
    SQUASHFS_MOUNT_OPTIONS,
    RegistryArtifactFormat,
    SquashfsArtifact,
    TarballArtifact,
    compute_registry_artifact_cache_key,
)
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock

_DOCKER_CHILD_ENV = "TRACECAT__EXECUTOR_ACTION_SMOKE_DOCKER_CHILD"
_SKIP_SENTINEL = "TRACE_CAT_EXECUTOR_ACTION_SMOKE_SKIP:"
_SMOKE_URI = "s3://tracecat-test-registry/smoke/site-packages.tar.gz"


class SmokeCase(StrEnum):
    DIRECT = "direct"
    DIRECT_SQUASHFS = "direct-squashfs"
    DIRECT_CURRENT_BUILTIN = "direct-current-builtin"
    NSJAIL_GZ = "nsjail-gz"
    NSJAIL_SQUASHFS = "nsjail-squashfs"
    NSJAIL_GATEWAY = "nsjail-gateway"
    NSJAIL_CURRENT_BUILTIN = "nsjail-current-builtin"

    @property
    def force_sandbox(self) -> bool:
        return self not in {
            SmokeCase.DIRECT,
            SmokeCase.DIRECT_SQUASHFS,
            SmokeCase.DIRECT_CURRENT_BUILTIN,
        }

    @property
    def preferred_format(self) -> RegistryArtifactFormat:
        if self in {SmokeCase.NSJAIL_SQUASHFS, SmokeCase.DIRECT_SQUASHFS}:
            return RegistryArtifactFormat.SQUASHFS
        return RegistryArtifactFormat.TAR_GZ


def _executor_nsjail_available() -> bool:
    return (
        Path(config.TRACECAT__SANDBOX_NSJAIL_PATH).is_file()
        and Path(config.TRACECAT__SANDBOX_ROOTFS_PATH).is_dir()
    )


def _kernel_supports_squashfs() -> bool:
    filesystems = Path("/proc/filesystems")
    if not filesystems.exists():
        return True
    return any(
        split_line[-1] == "squashfs"
        for line in filesystems.read_text().splitlines()
        if (split_line := line.split())
    )


def _skip_smoke(reason: str) -> NoReturn:
    if os.environ.get(_DOCKER_CHILD_ENV) == "1":
        print(f"{_SKIP_SENTINEL} {reason}")
        raise SystemExit(0)
    pytest.skip(reason)


def _missing_prerequisite(smoke_case: SmokeCase) -> str | None:
    if smoke_case.force_sandbox and not _executor_nsjail_available():
        return "executor nsjail unavailable"
    if smoke_case.force_sandbox and not Path("/dev/net/tun").exists():
        return "/dev/net/tun is unavailable for nsjail pasta networking"
    if smoke_case == SmokeCase.NSJAIL_SQUASHFS:
        if shutil.which("mksquashfs") is None:
            return "mksquashfs is unavailable"
        if shutil.which("mount") is None:
            return "mount is unavailable"
        if not _kernel_supports_squashfs():
            return "kernel does not advertise SquashFS support"
    if smoke_case == SmokeCase.DIRECT_SQUASHFS:
        if shutil.which("mksquashfs") is None:
            return "mksquashfs is unavailable"
        if shutil.which("unsquashfs") is None:
            return "unsquashfs is unavailable"
    return None


def _run_executor_action_smoke_in_docker_or_skip(smoke_case: SmokeCase) -> None:
    if os.environ.get(_DOCKER_CHILD_ENV) == "1":
        pytest.skip("executor nsjail unavailable inside Docker fallback child")
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable for executor nsjail fallback")

    docker_info = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if docker_info.returncode != 0:
        pytest.skip("Docker daemon unavailable for executor nsjail fallback")

    repo_root = Path(__file__).resolve().parents[2]
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
    compose_env.setdefault("TRACECAT__SERVICE_KEY", "test-service-key")

    tests_mount = f"{repo_root / 'tests'}:/app/tests:ro"
    device_lines = (
        [
            "    devices:",
            "      - /dev/net/tun:/dev/net/tun",
        ]
        if Path("/dev/net/tun").exists()
        else []
    )
    override_path = Path(
        tempfile.mkstemp(prefix="tracecat-executor-action-smoke-", suffix=".yml")[1]
    )
    override_path.write_text(
        "\n".join(
            [
                "services:",
                "  executor:",
                "    build:",
                "      target: test",
                "    privileged: true",
                "    security_opt:",
                "      - seccomp:unconfined",
                "      - systempaths=unconfined",
                *device_lines,
                "    volumes:",
                f"      - {json.dumps(tests_mount)}",
                "    environment:",
                f'      {_DOCKER_CHILD_ENV}: "1"',
                '      TRACECAT__DISABLE_NSJAIL: "false"',
                '      TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED: "true"',
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
                "executor",
                "-lc",
                "uv run python -m tests.unit.test_executor_sandbox_nsjail "
                f"--run-smoke {smoke_case.value}",
            ],
            cwd=repo_root,
            env=compose_env,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    finally:
        override_path.unlink(missing_ok=True)

    output = f"{result.stdout}\n{result.stderr}"
    if _SKIP_SENTINEL in output:
        reason = output.split(_SKIP_SENTINEL, maxsplit=1)[1].splitlines()[0].strip()
        pytest.skip(reason)

    if result.returncode != 0:
        pytest.fail(
            f"Dockerized executor action smoke failed for {smoke_case.value}."
            f"\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def _make_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


def _make_action_input(action_name: str) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            action=action_name,
            args={"value": "from-registry-artifact"},
            ref="registry_artifact_smoke",
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_squashfs_smoke",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"test_registry": "smoke"},
            actions={action_name: "test_registry"},
        ),
    )


def _make_builtin_action_input(action_name: str) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            action=action_name,
            args={"value": {"source": "current-builtin"}},
            ref="current_builtin_smoke",
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_current_builtin_smoke",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": tracecat_registry.__version__},
            actions={action_name: "tracecat_registry"},
        ),
    )


def _make_resolved_context(
    *,
    action_name: str,
    input: RunActionInput,
    role: Role,
) -> ResolvedContext:
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="udf",
            action_name=action_name,
            module="registry_artifact_smoke_action",
            name="run",
        ),
        evaluated_args={"value": "from-registry-artifact"},
        workspace_id=str(role.workspace_id),
        workflow_id=str(input.run_context.wf_id),
        run_id=str(input.run_context.wf_run_id),
        executor_token="test-executor-token",
        secret_projection=SecretEnvProjection(env={}, mask_values=set()),
    )


def _make_builtin_resolved_context(
    *,
    action_name: str,
    input: RunActionInput,
    role: Role,
) -> ResolvedContext:
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="udf",
            action_name=action_name,
            module="tracecat_registry.core.transform",
            name="reshape",
            origin="tracecat_registry",
        ),
        evaluated_args={"value": {"source": "current-builtin"}},
        workspace_id=str(role.workspace_id),
        workflow_id=str(input.run_context.wf_id),
        run_id=str(input.run_context.wf_run_id),
        executor_token="test-executor-token",
        secret_projection=SecretEnvProjection(env={}, mask_values=set()),
    )


def _write_registry_artifact_source(source_dir: Path) -> None:
    source_dir.mkdir(parents=True)
    (source_dir / "artifact_marker.txt").write_text("registry-artifact\n")
    (source_dir / "registry_artifact_smoke_action.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "def run(value: str) -> dict[str, str]:",
                "    marker = Path(__file__).with_name('artifact_marker.txt').read_text().strip()",
                "    return {'value': value, 'marker': marker, 'source': __file__}",
                "",
            ]
        )
    )


def _write_gateway_artifact_source(source_dir: Path) -> None:
    source_dir.mkdir(parents=True)
    (source_dir / "registry_artifact_smoke_action.py").write_text(
        "\n".join(
            [
                "import json",
                "import os",
                "import socket",
                "",
                "",
                "def _gateway_health() -> dict:",
                '    socket_path = os.environ["TRACECAT__ACTION_GATEWAY_SOCKET"]',
                "    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)",
                "    client.settimeout(5)",
                "    try:",
                "        client.connect(socket_path)",
                "        request = (",
                '            b"GET /internal/health HTTP/1.1\\r\\n"',
                '            b"Host: tracecat-action-gateway\\r\\n"',
                '            b"Connection: close\\r\\n\\r\\n"',
                "        )",
                "        client.sendall(request)",
                "        chunks = []",
                "        while True:",
                "            chunk = client.recv(4096)",
                "            if not chunk:",
                "                break",
                "            chunks.append(chunk)",
                "    finally:",
                "        client.close()",
                "",
                '    raw = b"".join(chunks).decode("utf-8")',
                '    headers, body = raw.split("\\r\\n\\r\\n", 1)',
                '    status_line = headers.split("\\r\\n", 1)[0]',
                '    status_code = int(status_line.split(" ", 2)[1])',
                "    return {",
                '        "status_code": status_code,',
                '        "body": json.loads(body),',
                '        "socket_path": socket_path,',
                "    }",
                "",
                "",
                "def run(value: str) -> dict:",
                "    return {",
                '        "value": value,',
                '        "gateway": _gateway_health(),',
                "    }",
                "",
            ]
        )
    )


def _add_source_dir_to_tar(tar: tarfile.TarFile, source_dir: Path) -> None:
    for path in sorted(source_dir.iterdir()):
        tar.add(path, arcname=path.name)


def _build_tar_gz(source_dir: Path, tarball_path: Path) -> None:
    with tarfile.open(tarball_path, "w:gz") as tar:
        _add_source_dir_to_tar(tar, source_dir)


def _build_squashfs_image(source_dir: Path, image_path: Path) -> None:
    result = subprocess.run(
        [
            "mksquashfs",
            str(source_dir),
            str(image_path),
            "-noappend",
            "-comp",
            "gzip",
            "-no-xattrs",
            "-all-root",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"mksquashfs failed\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def _unmount_if_needed(path: Path) -> None:
    if not path.is_mount():
        return
    subprocess.run(
        ["umount", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _squashfs_mount_probe_failure(image_path: Path, mount_dir: Path) -> str | None:
    mount_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "mount",
            "-t",
            "squashfs",
            "-o",
            SQUASHFS_MOUNT_OPTIONS,
            str(image_path),
            str(mount_dir),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    try:
        if result.returncode == 0 or mount_dir.is_mount():
            return None

        output = result.stderr.strip() or result.stdout.strip() or "mount failed"
        return f"SquashFS loop mount unavailable: {output}"
    finally:
        _unmount_if_needed(mount_dir)


async def _run_executor_action_smoke_case(
    smoke_case: SmokeCase,
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if reason := _missing_prerequisite(smoke_case):
        _skip_smoke(reason)

    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(config, "TRACECAT__API_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_SANDBOX_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 30.0)
    monkeypatch.setattr(config, "TRACECAT__ACTION_GATEWAY_ENABLED", False)
    if smoke_case is SmokeCase.NSJAIL_GATEWAY:
        monkeypatch.setattr(config, "TRACECAT__ACTION_GATEWAY_ENABLED", True)
        monkeypatch.setattr(
            config,
            "TRACECAT__ACTION_GATEWAY_SOCKET",
            str(tmp_path / "action-gateway.sock"),
        )

    source_dir = tmp_path / "site-packages"
    tar_gz_path = tmp_path / "site-packages.tar.gz"
    squashfs_path = tmp_path / "site-packages.squashfs"
    cache_dir = tmp_path / "registry-cache"
    if smoke_case is SmokeCase.NSJAIL_GATEWAY:
        _write_gateway_artifact_source(source_dir)
    else:
        _write_registry_artifact_source(source_dir)
    _build_tar_gz(source_dir, tar_gz_path)
    if smoke_case in {SmokeCase.NSJAIL_SQUASHFS, SmokeCase.DIRECT_SQUASHFS}:
        _build_squashfs_image(source_dir, squashfs_path)
        if reason := _squashfs_mount_probe_failure(
            squashfs_path,
            tmp_path / "squashfs-mount-probe",
        ):
            _skip_smoke(reason)

    runner = ActionRunner(cache_dir=cache_dir)
    cache_key = compute_registry_artifact_cache_key(_SMOKE_URI)
    mount_dir = cache_dir / f"squashfs-{cache_key}"
    extract_dir = cache_dir / f"unsquashfs-{cache_key}"
    tarball_dir = cache_dir / f"tarball-{cache_key}"

    async def sidecar_exists(
        *,
        base_uri: str,
        sidecar_uri: str,
        artifact_format: RegistryArtifactFormat,
    ) -> bool:
        return base_uri == _SMOKE_URI and artifact_format == smoke_case.preferred_format

    async def download_artifact(self, ctx, output_path: Path) -> float:
        if self.uri.endswith(".squashfs"):
            shutil.copy2(squashfs_path, output_path)
        else:
            shutil.copy2(tar_gz_path, output_path)
        return 0.0

    action_name = "test.registry_artifact_smoke"
    role = _make_role()
    action_input = _make_action_input(action_name)
    resolved_context = _make_resolved_context(
        action_name=action_name,
        input=action_input,
        role=role,
    )
    action_gateway: ActionGateway | None = None

    try:
        if smoke_case is SmokeCase.NSJAIL_GATEWAY:
            action_gateway = ActionGateway()
            await action_gateway.start()

        patches = [
            patch.object(runner.registry_artifacts, "_sidecar_exists", sidecar_exists),
            patch.object(SquashfsArtifact, "download", download_artifact),
            patch.object(TarballArtifact, "download", download_artifact),
        ]
        if smoke_case is SmokeCase.DIRECT_SQUASHFS:
            # Direct mode runs unprivileged; force the unsquashfs extraction
            # path instead of attempting a loopback mount.
            patches.append(
                patch(
                    "tracecat.executor.registry_artifacts.shutil.which",
                    return_value=None,
                )
            )

        with contextlib.ExitStack() as stack:
            for ctx_manager in patches:
                stack.enter_context(ctx_manager)
            result = await runner.execute_action(
                input=action_input,
                role=role,
                resolved_context=resolved_context,
                artifact_uris=[_SMOKE_URI],
                timeout=30,
                force_sandbox=smoke_case.force_sandbox,
            )

        if isinstance(result, ExecutorActionErrorInfo):
            raise AssertionError(f"Sandboxed action failed: {result}")

        assert isinstance(result, dict)
        assert result["value"] == "from-registry-artifact"
        if smoke_case is SmokeCase.NSJAIL_GATEWAY:
            assert result["gateway"] == {
                "status_code": 200,
                "body": {"status": "ok"},
                "socket_path": str(ACTION_GATEWAY_SANDBOX_SOCKET),
            }
            assert tarball_dir.exists()
            return

        assert result["marker"] == "registry-artifact"
        if smoke_case == SmokeCase.DIRECT:
            assert tarball_dir.exists()
            assert f"tarball-{cache_key}" in result["source"]
            assert result["source"].endswith("/registry_artifact_smoke_action.py")
        elif smoke_case == SmokeCase.DIRECT_SQUASHFS:
            assert extract_dir.exists()
            assert str(extract_dir) in result["source"]
            assert result["source"].endswith("/registry_artifact_smoke_action.py")
        else:
            assert result["source"] == "/packages/0/registry_artifact_smoke_action.py"
            if smoke_case == SmokeCase.NSJAIL_SQUASHFS:
                assert mount_dir.is_mount()
            else:
                assert tarball_dir.exists()
    finally:
        if action_gateway is not None:
            await action_gateway.stop()
        _unmount_if_needed(mount_dir)


async def _run_current_builtin_smoke_case(
    *,
    smoke_case: SmokeCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if reason := _missing_prerequisite(smoke_case):
        _skip_smoke(reason)

    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(config, "TRACECAT__API_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(
        config, "TRACECAT__EXECUTOR_SANDBOX_ENABLED", smoke_case.force_sandbox
    )
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 30.0)
    monkeypatch.setattr(config, "TRACECAT__ACTION_GATEWAY_ENABLED", False)

    action_name = "core.transform.reshape"
    role = _make_role()
    action_input = _make_builtin_action_input(action_name)
    resolved_context = _make_builtin_resolved_context(
        action_name=action_name,
        input=action_input,
        role=role,
    )

    backend = EphemeralBackend() if smoke_case.force_sandbox else DirectBackend()
    result = await backend.execute(
        input=action_input,
        role=role,
        resolved_context=resolved_context,
        timeout=30,
    )

    assert result.type == "success"
    assert result.result == {"source": "current-builtin"}


_CURRENT_BUILTIN_CASES = {
    SmokeCase.DIRECT_CURRENT_BUILTIN,
    SmokeCase.NSJAIL_CURRENT_BUILTIN,
}


@pytest.mark.parametrize(
    "smoke_case",
    [
        pytest.param(SmokeCase.DIRECT, id=SmokeCase.DIRECT.value),
        pytest.param(SmokeCase.DIRECT_SQUASHFS, id=SmokeCase.DIRECT_SQUASHFS.value),
        pytest.param(
            SmokeCase.DIRECT_CURRENT_BUILTIN,
            id=SmokeCase.DIRECT_CURRENT_BUILTIN.value,
        ),
        pytest.param(SmokeCase.NSJAIL_GZ, id=SmokeCase.NSJAIL_GZ.value),
        pytest.param(SmokeCase.NSJAIL_SQUASHFS, id=SmokeCase.NSJAIL_SQUASHFS.value),
        pytest.param(SmokeCase.NSJAIL_GATEWAY, id=SmokeCase.NSJAIL_GATEWAY.value),
        pytest.param(
            SmokeCase.NSJAIL_CURRENT_BUILTIN,
            id=SmokeCase.NSJAIL_CURRENT_BUILTIN.value,
        ),
    ],
)
@pytest.mark.anyio
async def test_action_runner_executes_registry_action_smoke(
    smoke_case: SmokeCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if smoke_case in _CURRENT_BUILTIN_CASES:
        if smoke_case.force_sandbox and _missing_prerequisite(smoke_case):
            _run_executor_action_smoke_in_docker_or_skip(smoke_case)
            return
        await _run_current_builtin_smoke_case(
            smoke_case=smoke_case, monkeypatch=monkeypatch
        )
        return

    if smoke_case.force_sandbox and _missing_prerequisite(smoke_case):
        _run_executor_action_smoke_in_docker_or_skip(smoke_case)
        return

    await _run_executor_action_smoke_case(
        smoke_case,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )


def _run_smoke_from_cli(smoke_case: SmokeCase) -> None:
    async def run() -> None:
        monkeypatch = pytest.MonkeyPatch()
        tmp_path = Path(tempfile.mkdtemp(prefix="tracecat-executor-nsjail-"))
        try:
            if smoke_case in _CURRENT_BUILTIN_CASES:
                await _run_current_builtin_smoke_case(
                    smoke_case=smoke_case, monkeypatch=monkeypatch
                )
            else:
                await _run_executor_action_smoke_case(
                    smoke_case,
                    monkeypatch=monkeypatch,
                    tmp_path=tmp_path,
                )
        finally:
            monkeypatch.undo()
            shutil.rmtree(tmp_path, ignore_errors=True)

    asyncio.run(run())


if __name__ == "__main__":
    if sys.argv[1:] == ["--run-nsjail-squashfs-smoke"]:
        _run_smoke_from_cli(SmokeCase.NSJAIL_SQUASHFS)
    elif len(sys.argv) == 3 and sys.argv[1] == "--run-smoke":
        _run_smoke_from_cli(SmokeCase(sys.argv[2]))
    else:
        raise SystemExit(
            "Usage: python -m tests.unit.test_executor_sandbox_nsjail "
            "--run-smoke "
            f"[{'|'.join(case.value for case in SmokeCase)}]"
        )
