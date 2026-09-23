"""Unit coverage for NsJail exit attribution without a structured result."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tracecat.sandbox.executor import (
    ActionSandboxConfig,
    NsjailExecutor,
    _classify_missing_nsjail_result,
    workload_stderr_tail,
)
from tracecat.sandbox.nsjail_protocol import NsjailCompletedProcess
from tracecat.sandbox.types import SandboxConfig, SandboxErrorCode

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
