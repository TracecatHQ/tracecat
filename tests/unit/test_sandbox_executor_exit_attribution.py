"""Unit coverage for NsJail exit attribution without a structured result."""

from __future__ import annotations

import asyncio
import errno
import io
import signal
from collections.abc import Callable
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tracecat.sandbox.exceptions import SandboxInfrastructureError
from tracecat.sandbox.executor import (
    ActionSandboxConfig,
    NsjailExecutor,
    SigkillEvidence,
    _classify_missing_nsjail_result,
    workload_stderr_tail,
)
from tracecat.sandbox.nsjail_protocol import (
    NsjailCompletedProcess,
    invoke_nsjail,
    nsjail_termination_signal,
    scan_nsjail_log,
)
from tracecat.sandbox.shutdown import worker_shutting_down
from tracecat.sandbox.types import SandboxConfig, SandboxErrorCode
from tracecat.temporal.worker_lifecycle import install_worker_shutdown_signal_handlers

_NSJAIL_PREAMBLE = (
    "[I][2026-01-01T00:00:00+0000] Mount: '/host/tmp/job' -> '/work' type:'' options:''\n"
    "[W][2026-01-01T00:00:00+0000] Uid map: inside_uid:1000 outside_uid:1001\n"
    "[I][2026-01-01T00:00:00+0000] Executing '/usr/local/bin/python3'\n"
)
_COROUTINE_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/work/minimal_runner.py", line 630, in serialize_result\n'
    "TypeError: Type is not JSON serializable: coroutine\n"
    "sys:1: RuntimeWarning: coroutine 'call_api' was never awaited\n"
)


def test_workload_stderr_tail_drops_nsjail_lines_and_keeps_traceback() -> None:
    tail = workload_stderr_tail(_NSJAIL_PREAMBLE + _COROUTINE_TRACEBACK, limit=8192)

    assert tail == _COROUTINE_TRACEBACK.strip()
    assert "/host/tmp/job" not in tail


def test_workload_stderr_tail_is_bounded_from_the_end() -> None:
    tail = workload_stderr_tail("x" * 100 + "TypeError: boom", limit=15)

    assert tail == "TypeError: boom"


def test_workload_stderr_tail_is_empty_without_workload_output() -> None:
    assert workload_stderr_tail(_NSJAIL_PREAMBLE, limit=8192) == ""


@pytest.mark.parametrize(
    ("returncode", "result_file_exists", "workload_started", "expected_code"),
    [
        pytest.param(
            0xFF,
            False,
            False,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="nsjail-launch-failure-before-workload-start",
        ),
        pytest.param(
            0xFF,
            False,
            True,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="workload-exit-255-after-start",
        ),
        pytest.param(
            -signal.SIGKILL,
            False,
            False,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="nsjail-parent-signal",
        ),
        pytest.param(
            128 + signal.SIGKILL,
            False,
            True,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="workload-memory-or-wall-limit",
        ),
        pytest.param(
            128 + signal.SIGXCPU,
            False,
            True,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="workload-cpu-limit",
        ),
        pytest.param(
            128 + signal.SIGXFSZ,
            False,
            True,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="workload-file-size-limit",
        ),
        pytest.param(
            128 + signal.SIGSYS,
            False,
            True,
            SandboxErrorCode.POLICY_VIOLATION,
            id="workload-seccomp-policy",
        ),
        # SIGABRT stays a workload failure on the core path: Python reports
        # allocation failure in-band as MemoryError (structured envelope code),
        # and an abort has other causes the exit code cannot separate.
        pytest.param(
            128 + signal.SIGABRT,
            False,
            True,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="workload-sigabrt-stays-workload-failure",
        ),
        pytest.param(
            1,
            False,
            True,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="workload-nonzero-exit",
        ),
        pytest.param(
            0,
            False,
            True,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="workload-zero-without-result",
        ),
        pytest.param(
            0xFF,
            True,
            False,
            SandboxErrorCode.WORKLOAD_FAILURE,
            id="malformed-result-proves-workload-ran",
        ),
    ],
)
def test_classify_missing_nsjail_result(
    returncode: int,
    result_file_exists: bool,
    workload_started: bool,
    expected_code: SandboxErrorCode,
) -> None:
    assert (
        _classify_missing_nsjail_result(
            returncode,
            result_file_exists=result_file_exists,
            workload_started=workload_started,
        )
        is expected_code
    )


@pytest.mark.anyio
@pytest.mark.parametrize("startup_chars", [0, 20_000])
@pytest.mark.parametrize("log_raw_crash_stderr", [False, True])
async def test_missing_result_preserves_crash_tail_and_launch_status(
    tmp_path: Path,
    mocker: MockerFixture,
    startup_chars: int,
    log_raw_crash_stderr: bool,
) -> None:
    crash = (
        'Fatal Python error: Segmentation fault\n  File "native_module.py", line 7\n'
    )
    stderr = "I" * startup_chars + crash
    mocker.patch(
        "tracecat.sandbox.executor.invoke_nsjail",
        return_value=NsjailCompletedProcess(
            returncode=139,
            stdout=b"",
            stderr=stderr.encode(),
            workload_started=True,
        ),
    )
    log_error = mocker.patch("tracecat.sandbox.executor.logger.error")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    mocker.patch.object(executor, "_build_config", return_value="")

    result = await executor.execute(
        tmp_path, SandboxConfig(), log_raw_crash_stderr=log_raw_crash_stderr
    )

    assert result.success is False
    assert result.exit_code == 139
    assert result.error_code is SandboxErrorCode.WORKLOAD_FAILURE
    assert result.stderr == stderr[-8192:]
    assert result.stderr.endswith(crash)
    assert result.error == "Sandbox workload exited without producing a result"
    fields = log_error.call_args.kwargs
    if log_raw_crash_stderr:
        assert fields["stderr"] == stderr[:500]
        assert fields["stderr_tail"] == result.stderr
    else:
        assert "stderr" not in fields
        assert "stderr_tail" not in fields
    assert fields["stderr_chars"] == len(stderr)
    assert fields["stderr_tail_truncated"] is (len(stderr) > 8192)
    assert fields["workload_started"] is True
    assert fields["result_file_exists"] is False


@pytest.mark.anyio
async def test_action_missing_result_keeps_workload_traceback_tail(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "tracecat.sandbox.executor.invoke_nsjail",
        return_value=NsjailCompletedProcess(
            returncode=1,
            stdout=b"",
            stderr=(_NSJAIL_PREAMBLE + _COROUTINE_TRACEBACK).encode(),
            workload_started=True,
        ),
    )
    mocker.patch("tracecat.sandbox.executor.logger.error")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    mocker.patch.object(executor, "_build_action_config", return_value="")
    mocker.patch.object(executor, "_build_action_env_map", return_value={})
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    action_config = ActionSandboxConfig(
        registry_paths=[],
        tracecat_app_dir=tmp_path,
        site_packages_dir=None,
    )

    result = await executor.execute_action(job_dir, action_config)

    assert result.success is False
    assert result.error_code is SandboxErrorCode.WORKLOAD_FAILURE
    assert result.stderr == _COROUTINE_TRACEBACK.strip()


@pytest.mark.anyio
async def test_script_crash_does_not_log_injected_secrets(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    env_vars = {
        "TRACECAT__EXECUTOR_TOKEN": "synthetic-executor-token",
        "USER_SECRET": "synthetic-user-secret",
    }
    stderr = "\n".join(env_vars.values())
    mocker.patch(
        "tracecat.sandbox.executor.invoke_nsjail",
        return_value=NsjailCompletedProcess(
            returncode=1,
            stdout=b"",
            stderr=stderr.encode(),
            workload_started=True,
        ),
    )
    log_error = mocker.patch("tracecat.sandbox.executor.logger.error")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    mocker.patch.object(executor, "_build_config", return_value="")

    result = await executor.execute(tmp_path, SandboxConfig(env_vars=env_vars))

    assert result.stderr == stderr
    log_error.assert_called_once()
    for secret in env_vars.values():
        assert secret not in str(log_error.call_args)
    assert log_error.call_args.kwargs["returncode"] == 1


def _evidence(
    *,
    termination_signal: int | None = signal.SIGKILL,
    oom_kills: int | None = 0,
    time_limit_kill: bool = False,
    worker_shutting_down: bool = True,
) -> SigkillEvidence:
    return SigkillEvidence(
        termination_signal=termination_signal,
        oom_kills=oom_kills,
        time_limit_kill=time_limit_kill,
        worker_shutting_down=worker_shutting_down,
    )


@pytest.mark.parametrize(
    ("kill_evidence", "expected_code"),
    [
        pytest.param(
            _evidence(),
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="sigkill-during-worker-shutdown",
        ),
        pytest.param(
            None,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="no-evidence",
        ),
        pytest.param(
            _evidence(worker_shutting_down=False),
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="worker-not-shutting-down",
        ),
        pytest.param(
            _evidence(termination_signal=None),
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="workload-exit-137",
        ),
        pytest.param(
            _evidence(oom_kills=1),
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="cgroup-oom-kill",
        ),
        pytest.param(
            _evidence(oom_kills=None),
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="oom-counter-unreadable",
        ),
        pytest.param(
            _evidence(time_limit_kill=True),
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="nsjail-time-limit-kill",
        ),
    ],
)
def test_classify_sigkill_requires_trusted_external_kill_evidence(
    kill_evidence: SigkillEvidence | None,
    expected_code: SandboxErrorCode,
) -> None:
    assert (
        _classify_missing_nsjail_result(
            128 + signal.SIGKILL,
            result_file_exists=False,
            workload_started=True,
            kill_evidence=kill_evidence,
        )
        is expected_code
    )


def test_external_kill_evidence_does_not_change_other_signals() -> None:
    assert (
        _classify_missing_nsjail_result(
            128 + signal.SIGXCPU,
            result_file_exists=False,
            workload_started=True,
            kill_evidence=_evidence(termination_signal=signal.SIGXCPU),
        )
        is SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED
    )


@pytest.mark.parametrize(
    ("nsjail_log", "expected_signal"),
    [
        pytest.param(
            b"[I][t] pid=9 ([STANDALONE MODE]) terminated with signal: SIGKILL (9), "
            b"(PIDs left: 0)\n",
            signal.SIGKILL,
            id="signaled",
        ),
        pytest.param(
            b"[I][t] pid=9 ([STANDALONE MODE]) exited with status: 137, "
            b"(PIDs left: 0)\n",
            None,
            id="exited-137",
        ),
        pytest.param(b"", None, id="empty-log"),
    ],
)
def test_nsjail_termination_signal_reads_nsjail_log(
    nsjail_log: bytes, expected_signal: int | None
) -> None:
    assert nsjail_termination_signal(nsjail_log) == expected_signal


def test_nsjail_log_scan_keeps_termination_record_after_noisy_log() -> None:
    warning = b"[W][t] Maximum number of UDP flows reached, dropping packet\n"
    final = b"[I][t] pid=9 ([STANDALONE MODE]) terminated with signal: SIGKILL (9)\n"
    noisy_log = io.BytesIO(warning * (2 * 1024 * 1024 // len(warning)) + final)

    scan = scan_nsjail_log(noisy_log)

    assert scan.termination_signal == signal.SIGKILL
    assert scan.time_limit_kill is False
    assert len(scan.tail) <= 1024 * 1024
    assert scan.tail.endswith(final)


@pytest.mark.anyio
async def test_nsjail_log_creation_failure_is_infrastructure_error(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch(
        "tracecat.sandbox.nsjail_protocol.tempfile.TemporaryFile",
        side_effect=OSError(errno.EMFILE, "Too many open files"),
    )
    create_process = mocker.patch("asyncio.create_subprocess_exec")

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


def _write_run_cgroup_oom_kills(root: Path, oom_kills: int) -> None:
    (run_cgroup,) = (path for path in root.iterdir() if path.name.startswith("run-"))
    (run_cgroup / "memory.events").write_text(
        f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kills}\noom_group_kill 0\n"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "termination_signal",
        "oom_kills",
        "time_limit_kill",
        "shutting_down",
        "expected_code",
    ),
    [
        pytest.param(
            signal.SIGKILL,
            0,
            False,
            True,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="sigkill-during-worker-shutdown",
        ),
        pytest.param(
            signal.SIGKILL,
            0,
            False,
            False,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="worker-not-shutting-down",
        ),
        pytest.param(
            signal.SIGKILL,
            1,
            False,
            True,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="run-cgroup-oom-kill",
        ),
        pytest.param(
            signal.SIGKILL,
            0,
            True,
            True,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="nsjail-time-limit-kill",
        ),
        pytest.param(
            None,
            0,
            False,
            True,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="workload-exit-137",
        ),
    ],
)
async def test_action_sigkill_attribution_uses_trusted_evidence(
    tmp_path: Path,
    mocker: MockerFixture,
    termination_signal: int | None,
    oom_kills: int,
    time_limit_kill: bool,
    shutting_down: bool,
    expected_code: SandboxErrorCode,
) -> None:
    """Only a SIGKILL while this worker shuts down is attributed to the platform."""
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()

    async def killed_workload(**_: object) -> NsjailCompletedProcess:
        _write_run_cgroup_oom_kills(cgroup, oom_kills)
        return NsjailCompletedProcess(
            returncode=128 + signal.SIGKILL,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            termination_signal=termination_signal,
            time_limit_kill=time_limit_kill,
        )

    mocker.patch("tracecat.sandbox.executor.invoke_nsjail", side_effect=killed_workload)
    mocker.patch(
        "tracecat.sandbox.executor.worker_shutting_down", return_value=shutting_down
    )
    mocker.patch("tracecat.sandbox.executor.logger")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"), cgroup_mount=cgroup)
    build_config = mocker.patch.object(
        executor, "_build_action_config", return_value=""
    )
    mocker.patch.object(executor, "_build_action_env_map", return_value={})
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    action_config = ActionSandboxConfig(
        registry_paths=[],
        tracecat_app_dir=tmp_path,
        site_packages_dir=None,
    )

    result = await executor.execute_action(job_dir, action_config)

    assert result.success is False
    assert result.exit_code == 128 + signal.SIGKILL
    assert result.error_code is expected_code
    run_cgroup = build_config.call_args.kwargs["cgroup_path"]
    assert run_cgroup.parent == cgroup
    assert run_cgroup.name.startswith("run-")


@pytest.mark.anyio
async def test_script_sigkill_during_worker_shutdown_is_infrastructure_failure(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()

    async def killed_workload(**_: object) -> NsjailCompletedProcess:
        _write_run_cgroup_oom_kills(cgroup, 0)
        return NsjailCompletedProcess(
            returncode=128 + signal.SIGKILL,
            stdout=b"",
            stderr=b"",
            workload_started=True,
            termination_signal=signal.SIGKILL,
        )

    mocker.patch("tracecat.sandbox.executor.invoke_nsjail", side_effect=killed_workload)
    mocker.patch("tracecat.sandbox.executor.worker_shutting_down", return_value=True)
    mocker.patch("tracecat.sandbox.executor.logger")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"), cgroup_mount=cgroup)
    mocker.patch.object(executor, "_build_config", return_value="")

    result = await executor.execute(tmp_path, SandboxConfig())

    assert result.error_code is SandboxErrorCode.INFRASTRUCTURE_FAILURE


class _RecordingLoop:
    def __init__(self) -> None:
        self.handlers: dict[int, tuple[Callable[..., object], tuple[object, ...]]] = {}

    def add_signal_handler(
        self, sig: int, callback: Callable[..., object], *args: object
    ) -> None:
        self.handlers[sig] = (callback, args)

    def remove_signal_handler(self, sig: int) -> bool:
        return self.handlers.pop(sig, None) is not None


def test_worker_shutdown_signal_marks_sandbox_shutdown(mocker: MockerFixture) -> None:
    mocker.patch("tracecat.sandbox.shutdown._shutting_down", False)
    shutdown_event = asyncio.Event()
    loop = _RecordingLoop()

    with install_worker_shutdown_signal_handlers(shutdown_event, loop=loop):
        assert worker_shutting_down() is False
        callback, args = loop.handlers[signal.SIGTERM]
        callback(*args)

    assert worker_shutting_down() is True
    assert shutdown_event.is_set()
