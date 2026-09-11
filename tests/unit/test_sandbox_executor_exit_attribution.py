"""Unit coverage for NsJail exit attribution without a structured result."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tracecat.sandbox.executor import NsjailExecutor, _classify_missing_nsjail_result
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
async def test_missing_result_preserves_crash_tail_and_launch_status(
    tmp_path: Path, mocker: MockerFixture, startup_chars: int
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

    result = await executor.execute(tmp_path, SandboxConfig())

    assert result.success is False
    assert result.exit_code == 139
    assert result.error_code is SandboxErrorCode.WORKLOAD_FAILURE
    assert result.stderr == stderr[-8192:]
    assert result.stderr.endswith(crash)
    assert result.error == "Sandbox workload exited without producing a result"
    fields = log_error.call_args.kwargs
    assert fields["stderr"] == stderr[:500]
    assert fields["stderr_tail"] == result.stderr
    assert fields["stderr_chars"] == len(stderr)
    assert fields["stderr_tail_truncated"] is (len(stderr) > 8192)
    assert fields["workload_started"] is True
    assert fields["result_file_exists"] is False
