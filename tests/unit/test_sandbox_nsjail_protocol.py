"""Host-side nsjail setup and launch attribution tests."""

from __future__ import annotations

import errno
import io
from pathlib import Path
from typing import NoReturn, cast
from unittest.mock import AsyncMock

import pytest

from tracecat.sandbox import executor as executor_module
from tracecat.sandbox import nsjail_protocol
from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxInfrastructureError,
    SandboxTimeoutError,
)
from tracecat.sandbox.executor import (
    _WORKLOAD_LAUNCHER_NAME,
    _WORKLOAD_LAUNCHER_SCRIPT,
    _WORKLOAD_STARTED_MARKER,
    ActionSandboxConfig,
    NsjailExecutor,
)
from tracecat.sandbox.nsjail_protocol import (
    NsjailCompletedProcess,
    invoke_nsjail,
    scan_nsjail_log,
)
from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import SandboxConfig, SandboxErrorCode, SandboxResult


@pytest.mark.anyio
async def test_missing_nsjail_binary_is_infrastructure_failure(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "missing-nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )

    with pytest.raises(SandboxInfrastructureError) as exc_info:
        await executor.execute_action(
            job_dir,
            ActionSandboxConfig(
                registry_paths=[],
                tracecat_app_dir=tmp_path,
                network=None,
            ),
        )

    assert isinstance(exc_info.value.__cause__, FileNotFoundError)
    assert not (job_dir / "nsjail.cfg").exists()


@pytest.mark.anyio
async def test_executor_owned_file_write_is_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write_text = Path.write_text

    def fail_config_write(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name == "nsjail.cfg":
            raise OSError("synthetic host filesystem failure")
        return original_write_text(
            path,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", fail_config_write)

    with pytest.raises(SandboxInfrastructureError) as exc_info:
        await invoke_nsjail(
            nsjail_path=tmp_path / "missing-nsjail",
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
        )

    assert isinstance(exc_info.value.__cause__, OSError)
    assert not (tmp_path / "nsjail.cfg").exists()


@pytest.mark.anyio
async def test_timeout_is_reported_and_config_cleaned_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        nsjail_protocol,
        "communicate_process_group",
        raise_timeout,
    )

    with pytest.raises(SandboxTimeoutError, match="synthetic timeout"):
        await invoke_nsjail(
            nsjail_path=Path("/bin/sh"),
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
        )

    assert not (tmp_path / "nsjail.cfg").exists()


async def _run_phase(tmp_path: Path, job_dir: Path, phase: str) -> SandboxResult:
    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )
    if phase == "install":
        return await executor.execute_install(job_dir, "deadbeef")
    if phase == "execute":
        return await executor.execute(job_dir, SandboxConfig())
    return await executor.execute_action(
        job_dir,
        ActionSandboxConfig(
            registry_paths=[],
            tracecat_app_dir=tmp_path,
            network=None,
        ),
    )


@pytest.mark.parametrize(
    ("phase", "expected_script"),
    [
        pytest.param("install", "/work/install.py", id="install"),
        pytest.param("execute", "/work/wrapper.py", id="script"),
        pytest.param("action", "/work/minimal_runner.py", id="action"),
    ],
)
@pytest.mark.parametrize(
    ("workload_started", "expected_code"),
    [
        pytest.param(
            False,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="launch-failure-before-workload-start",
        ),
        pytest.param(
            True,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="workload-exit-255-after-start",
        ),
    ],
)
@pytest.mark.anyio
async def test_invocation_marker_attributes_exit_255_for_every_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expected_script: str,
    workload_started: bool,
    expected_code: SandboxErrorCode,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()

    async def fake_invoke_nsjail(**kwargs: object) -> NsjailCompletedProcess:
        config_text = cast(str, kwargs["config_text"])
        assert f"/work/{_WORKLOAD_LAUNCHER_NAME}" in config_text
        assert expected_script in config_text
        assert kwargs["workload_launcher_name"] == _WORKLOAD_LAUNCHER_NAME
        assert kwargs["workload_launcher_script"] == _WORKLOAD_LAUNCHER_SCRIPT
        assert kwargs["workload_started_marker"] == _WORKLOAD_STARTED_MARKER
        return NsjailCompletedProcess(
            returncode=0xFF,
            stdout=b"",
            stderr=b"",
            workload_started=workload_started,
            nsjail_log=b"",
            nsjail_signaled=False,
        )

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    result = await _run_phase(tmp_path, job_dir, phase)

    assert result.success is False
    assert result.error_code is expected_code


@pytest.mark.anyio
async def test_nsjail_private_log_is_passed_at_info_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched_configs: list[str] = []
    launched_fds: list[int] = []

    async def capture_launch(*args: str, **kwargs: object) -> NoReturn:
        launched_configs.append(Path(args[2]).read_text())
        launched_fds.extend(cast(tuple[int, ...], kwargs["pass_fds"]))
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(
        nsjail_protocol.asyncio, "create_subprocess_exec", capture_launch
    )

    with pytest.raises(SandboxInfrastructureError):
        await invoke_nsjail(
            nsjail_path=tmp_path / "nsjail",
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
        )

    (log_fd,) = launched_fds
    (config_text,) = launched_configs
    assert f"\nlog_fd: {log_fd}\n" in config_text
    assert "\nlog_level: INFO\n" in config_text


_FATAL_SIGNAL_RECORD = (
    b"[I][2026-10-19T18:03:23+0000] Server stops due to fatal signal (15) caught. "
    b"Exiting\n"
)


@pytest.mark.parametrize(
    ("nsjail_log", "expected"),
    [
        pytest.param(_FATAL_SIGNAL_RECORD, True, id="fatal-signal-record"),
        pytest.param(
            b"[I][t] pid=9 ([STANDALONE MODE]) terminated with signal: SIGKILL (9), "
            b"(PIDs left: 0)\n",
            False,
            id="sigkill-without-record",
        ),
        pytest.param(
            b"[W][t][9] f():1 Server stops due to fatal signal (15) caught\n",
            False,
            id="record-text-in-other-message",
        ),
        pytest.param(b"", False, id="empty-log"),
    ],
)
def test_nsjail_log_scan_reads_fatal_signal_record(
    nsjail_log: bytes, expected: bool
) -> None:
    assert scan_nsjail_log(io.BytesIO(nsjail_log)).nsjail_signaled is expected


def test_nsjail_log_scan_keeps_record_and_bounded_tail_after_noisy_log() -> None:
    warning = b"[W][t] Maximum number of UDP flows reached, dropping packet\n"
    noisy_log = io.BytesIO(
        _FATAL_SIGNAL_RECORD + warning * (2 * 1024 * 1024 // len(warning))
    )

    scan = scan_nsjail_log(noisy_log)

    assert scan.nsjail_signaled is True
    assert len(scan.tail) == 1024 * 1024
    assert scan.tail.endswith(warning)


@pytest.mark.anyio
async def test_nsjail_log_creation_failure_is_infrastructure_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_log_creation() -> NoReturn:
        raise OSError(errno.EMFILE, "Too many open files")

    create_process = AsyncMock()
    monkeypatch.setattr(nsjail_protocol.tempfile, "TemporaryFile", fail_log_creation)
    monkeypatch.setattr(
        nsjail_protocol.asyncio, "create_subprocess_exec", create_process
    )

    with pytest.raises(SandboxInfrastructureError):
        await invoke_nsjail(
            nsjail_path=tmp_path / "nsjail",
            job_dir=tmp_path,
            config_text="",
            env={},
            timeout_seconds=1,
            timeout_message="timed out",
        )
    create_process.assert_not_called()


@pytest.mark.parametrize("phase", ["install", "execute", "action"])
@pytest.mark.parametrize(
    ("nsjail_signaled", "expected_code"),
    [
        pytest.param(True, SandboxErrorCode.INFRASTRUCTURE_FAILURE, id="signaled"),
        pytest.param(False, SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED, id="unsignaled"),
    ],
)
@pytest.mark.anyio
async def test_nsjail_fatal_signal_attributes_exit_137_for_every_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    nsjail_signaled: bool,
    expected_code: SandboxErrorCode,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    # NSTUN lines mention "tun", which must not add a TUN hint to a drain kill.
    nsjail_log = b"[I][t] nstun: started\n"
    if nsjail_signaled:
        nsjail_log += _FATAL_SIGNAL_RECORD

    async def fake_invoke_nsjail(**kwargs: object) -> NsjailCompletedProcess:
        return NsjailCompletedProcess(
            returncode=137,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            nsjail_log=nsjail_log,
            nsjail_signaled=nsjail_signaled,
        )

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    result = await _run_phase(tmp_path, job_dir, phase)

    assert result.success is False
    assert result.exit_code == 137
    assert result.error_code is expected_code
    if nsjail_signaled:
        assert "/dev/net/tun" not in (result.error or "")
        if phase != "install":
            assert result.error == "Sandbox host stopped before the workload finished"


@pytest.mark.parametrize("phase", ["execute", "action"])
@pytest.mark.parametrize(
    ("nsjail_signaled", "expected_code"),
    [
        pytest.param(True, SandboxErrorCode.INFRASTRUCTURE_FAILURE, id="signaled"),
        pytest.param(False, SandboxErrorCode.WORKLOAD_FAILURE, id="unsignaled"),
    ],
)
@pytest.mark.anyio
async def test_drain_kill_during_result_write_is_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    nsjail_signaled: bool,
    expected_code: SandboxErrorCode,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "result.json").write_text('{"success": tr')

    async def fake_invoke_nsjail(**kwargs: object) -> NsjailCompletedProcess:
        return NsjailCompletedProcess(
            returncode=137,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            nsjail_log=_FATAL_SIGNAL_RECORD if nsjail_signaled else b"",
            nsjail_signaled=nsjail_signaled,
        )

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    result = await _run_phase(tmp_path, job_dir, phase)

    assert result.success is False
    assert result.error_code is expected_code


@pytest.mark.parametrize(
    "untrusted_code",
    ["synthetic.unknown", SandboxErrorCode.INFRASTRUCTURE_FAILURE],
)
@pytest.mark.parametrize("phase", ["execute", "action"])
@pytest.mark.anyio
async def test_untrusted_result_error_code_cannot_claim_platform_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    untrusted_code: str,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "result.json").write_text(
        f'{{"success": false, "error_code": "{untrusted_code}"}}'
    )

    async def fake_invoke_nsjail(**kwargs: object) -> NsjailCompletedProcess:
        return NsjailCompletedProcess(
            returncode=0xFF,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            nsjail_log=b"",
            nsjail_signaled=False,
        )

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    result = await _run_phase(tmp_path, job_dir, phase)

    assert result.success is False
    assert result.error_code is SandboxErrorCode.WORKLOAD_FAILURE


@pytest.mark.anyio
async def test_workload_start_proof_is_scoped_to_each_nsjail_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = 0

    class FakeProcess:
        returncode = 0xFF

    async def fake_create_subprocess_exec(
        *args: object, **kwargs: object
    ) -> FakeProcess:
        launcher_path = tmp_path / _WORKLOAD_LAUNCHER_NAME
        assert launcher_path.read_text() == _WORKLOAD_LAUNCHER_SCRIPT
        return FakeProcess()

    async def fake_communicate_process_group(
        process: object,
        *,
        timeout: float,
        input: bytes | None = None,  # noqa: A002
    ) -> tuple[bytes, bytes]:
        nonlocal invocation
        invocation += 1
        if invocation == 1:
            return b"first stdout", _WORKLOAD_STARTED_MARKER + b"first stderr"
        return b"second stdout", b"second stderr"

    monkeypatch.setattr(
        nsjail_protocol.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        nsjail_protocol,
        "communicate_process_group",
        fake_communicate_process_group,
    )

    async def run_once() -> NsjailCompletedProcess:
        return await invoke_nsjail(
            nsjail_path=tmp_path / "nsjail",
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={},
            timeout_seconds=1,
            timeout_message="synthetic timeout",
            workload_launcher_name=_WORKLOAD_LAUNCHER_NAME,
            workload_launcher_script=_WORKLOAD_LAUNCHER_SCRIPT,
            workload_started_marker=_WORKLOAD_STARTED_MARKER,
        )

    first = await run_once()
    # A workload-controlled file from an earlier phase must not affect the
    # next invocation's attribution.
    (tmp_path / ".tracecat-workload-started").touch()
    second = await run_once()

    assert first.workload_started is True
    assert first.stderr == b"first stderr"
    assert second.workload_started is False
    assert second.stderr == b"second stderr"
    assert not (tmp_path / "nsjail.cfg").exists()
    assert not (tmp_path / _WORKLOAD_LAUNCHER_NAME).exists()


@pytest.mark.parametrize(
    ("error_code", "expected_exception"),
    [
        pytest.param(
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            SandboxInfrastructureError,
            id="infrastructure-failure",
        ),
        pytest.param(
            SandboxErrorCode.WORKLOAD_FAILURE,
            PackageInstallError,
            id="package-failure",
        ),
    ],
)
@pytest.mark.anyio
async def test_package_install_preserves_structural_failure_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_code: SandboxErrorCode,
    expected_exception: type[Exception],
) -> None:
    service = SandboxService(cache_dir=str(tmp_path / "sandbox-cache"))
    monkeypatch.setattr(
        service, "_nsjail_executor", NsjailExecutor(cgroup_mount=tmp_path / "cgroup")
    )
    monkeypatch.setattr(
        service.nsjail_executor,
        "execute_install",
        AsyncMock(
            return_value=SandboxResult(
                success=False,
                error="synthetic install failure",
                error_code=error_code,
            )
        ),
    )

    with pytest.raises(expected_exception):
        await service._install_packages(
            tmp_path,
            ["synthetic-package==1.0.0"],
            "deadbeef",
        )
