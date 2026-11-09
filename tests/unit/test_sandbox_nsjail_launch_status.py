"""Real NsJail process status coverage for launch failures and child signals."""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from tracecat.config import TRACECAT__SANDBOX_NSJAIL_PATH
from tracecat.sandbox.executor import _classify_missing_nsjail_result
from tracecat.sandbox.types import SandboxErrorCode


@pytest.fixture
def nsjail_binary() -> str:
    if sys.platform != "linux":
        pytest.skip("NsJail process status tests require Linux")
    if binary := shutil.which(TRACECAT__SANDBOX_NSJAIL_PATH):
        return binary
    pytest.skip("NsJail process status tests require the built NsJail binary")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("exec_config", "mount_config", "expected_status", "expected_code"),
    [
        pytest.param(
            'exec_bin { path: "/bin/sh" arg: "-c" arg: "exit 0" }',
            "",
            0,
            None,
            id="healthy-child",
        ),
        pytest.param(
            'exec_bin { path: "/synthetic-missing-executable" }',
            "",
            255,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="exec-failure",
        ),
        pytest.param(
            'exec_bin { path: "/bin/sh" arg: "-c" arg: "exit 0" }',
            'mount { src: "/synthetic-missing-mount" dst: "/work" is_bind: true }',
            255,
            SandboxErrorCode.INFRASTRUCTURE_FAILURE,
            id="mandatory-mount-failure",
        ),
        pytest.param(
            'exec_bin { path: "/bin/sh" arg: "-c" arg: "kill -KILL $$" }',
            "",
            128 + signal.SIGKILL,
            SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED,
            id="child-sigkill-without-start-marker",
        ),
    ],
)
def test_nsjail_preserves_launch_and_signal_status(
    tmp_path: Path,
    nsjail_binary: str,
    exec_config: str,
    mount_config: str,
    expected_status: int,
    expected_code: SandboxErrorCode | None,
) -> None:
    config_path = tmp_path / "nsjail.cfg"
    config_path.write_text(
        "\n".join(
            [
                "mode: ONCE",
                "clone_newnet: false",
                "clone_newuser: false",
                f"clone_newns: {'true' if mount_config else 'false'}",
                "clone_newpid: false",
                "clone_newipc: false",
                "clone_newuts: false",
                "time_limit: 5",
                "rlimit_as_type: SOFT",
                "rlimit_cpu_type: SOFT",
                "rlimit_fsize_type: SOFT",
                "rlimit_nofile_type: SOFT",
                "rlimit_nproc_type: SOFT",
                mount_config,
                exec_config,
            ]
        )
    )
    completed = subprocess.run(
        [nsjail_binary, "--config", str(config_path)],
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == expected_status, completed.stderr.decode()
    if expected_code is not None:
        assert (
            _classify_missing_nsjail_result(
                completed.returncode, result_file_exists=False, workload_started=False
            )
            is expected_code
        )
