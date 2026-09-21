"""Unit coverage for NsJail exit attribution without a structured result."""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tracecat.sandbox.executor import (
    ActionSandboxConfig,
    NsjailExecutor,
    _classify_missing_nsjail_result,
)
from tracecat.sandbox.nsjail_protocol import NsjailCompletedProcess
from tracecat.sandbox.types import SandboxConfig, SandboxErrorCode


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


@pytest.mark.anyio
async def test_package_install_failure_does_not_log_sandbox_output(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Installer diagnostics stay in the result for caller-side masking."""
    secret = "synthetic-install-secret"
    stdout = f"downloaded {secret}"
    stderr = f"index response {secret}"
    mocker.patch(
        "tracecat.sandbox.executor.invoke_nsjail",
        return_value=NsjailCompletedProcess(
            returncode=1,
            stdout=stdout.encode(),
            stderr=stderr.encode(),
            workload_started=True,
        ),
    )
    log_error = mocker.patch("tracecat.sandbox.executor.logger.error")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    mocker.patch.object(executor, "_build_config", return_value="")
    mocker.patch.object(executor, "_build_env_map", return_value={})

    result = await executor.execute_install(tmp_path, "deadbeef")

    assert result.success is False
    assert result.error == stderr
    log_error.assert_called_once()
    fields = log_error.call_args.kwargs
    assert set(fields) == {
        "returncode",
        "error_code",
        "stdout_chars",
        "stderr_chars",
        "workload_started",
        "execution_time_ms",
    }
    assert fields["error_code"] is SandboxErrorCode.WORKLOAD_FAILURE
    assert fields["returncode"] == 1
    assert fields["stdout_chars"] == len(stdout)
    assert fields["stderr_chars"] == len(stderr)
    assert secret not in repr(log_error.call_args)


@pytest.mark.anyio
async def test_action_failure_does_not_log_sandbox_output(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Action errors and subprocess streams never enter ordinary logs."""
    secret = "synthetic-action-secret"
    stderr = f"action log {secret}"
    error = {"type": "ValueError", "message": secret}
    (tmp_path / "result.json").write_text(
        json.dumps({"success": False, "result": None, "error": error})
    )
    mocker.patch(
        "tracecat.sandbox.executor.invoke_nsjail",
        return_value=NsjailCompletedProcess(
            returncode=1,
            stdout=b"",
            stderr=stderr.encode(),
            workload_started=True,
        ),
    )
    log_debug = mocker.patch("tracecat.sandbox.executor.logger.debug")
    log_info = mocker.patch("tracecat.sandbox.executor.logger.info")
    log_error = mocker.patch("tracecat.sandbox.executor.logger.error")
    executor = NsjailExecutor(cache_dir=str(tmp_path / "cache"))
    mocker.patch.object(executor, "_build_action_config", return_value="")
    mocker.patch.object(executor, "_build_action_env_map", return_value={})

    result = await executor.execute_action(
        tmp_path,
        ActionSandboxConfig(
            registry_paths=[],
            tracecat_app_dir=tmp_path,
            network=None,
        ),
    )

    assert result.success is False
    assert result.error == error
    assert result.stderr == stderr
    log_info.assert_not_called()
    log_error.assert_not_called()
    assert log_debug.call_count == 2
    for call in log_debug.call_args_list:
        assert secret not in repr(call)
    assert log_debug.call_args_list[-1].kwargs == {
        "stdout_chars": 0,
        "stderr_chars": len(stderr),
        "execution_time_ms": result.execution_time_ms,
    }


@pytest.mark.anyio
async def test_action_missing_result_does_not_log_sandbox_output(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Missing-result diagnostics remain safe when action startup fails."""
    secret = "synthetic-action-crash-secret"
    stderr = f"fatal action output {secret}"
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
    mocker.patch.object(executor, "_build_action_config", return_value="")
    mocker.patch.object(executor, "_build_action_env_map", return_value={})

    result = await executor.execute_action(
        tmp_path,
        ActionSandboxConfig(
            registry_paths=[],
            tracecat_app_dir=tmp_path,
            network=None,
        ),
    )

    assert result.success is False
    assert result.error_code is SandboxErrorCode.WORKLOAD_FAILURE
    assert result.stderr == stderr[:2000]
    log_error.assert_called_once()
    fields = log_error.call_args.kwargs
    assert set(fields) == {
        "error_code",
        "returncode",
        "stdout_chars",
        "stderr_chars",
        "workload_started",
        "result_file_exists",
        "execution_time_ms",
    }
    assert fields["error_code"] is SandboxErrorCode.WORKLOAD_FAILURE
    assert fields["stderr_chars"] == len(stderr)
    assert secret not in repr(log_error.call_args)
