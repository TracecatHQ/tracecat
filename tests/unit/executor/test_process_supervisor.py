"""Linux process-tree containment for direct registry actions."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest

from tracecat.executor import process_supervisor
from tracecat.sandbox.utils import (
    communicate_process_group,
    terminate_supervised_process,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="The direct action supervisor uses Linux prctl and procfs",
)


def _process_is_running(pid: int) -> bool:
    """Return whether a process is alive, treating zombies as terminated."""
    try:
        os.kill(pid, 0)
        stat_path = Path(f"/proc/{pid}/stat")
        return not stat_path.exists() or stat_path.read_text().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


async def _wait_for_file(path: Path) -> None:
    for _ in range(500):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {path}")


def _write_action_script(path: Path) -> None:
    path.write_text(
        """
import os
import subprocess
import sys
import time
from pathlib import Path

pid_file = Path(sys.argv[1])
mode = sys.argv[2]
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
pid_file.write_text(f"{os.getpid()} {child.pid}")
if mode == "failure":
    raise SystemExit(23)
if mode == "block":
    time.sleep(30)
""".lstrip()
    )


async def _spawn_supervised_action(
    tmp_path: Path,
    *,
    mode: str,
) -> tuple[asyncio.subprocess.Process, Path]:
    action_script = tmp_path / f"action-{mode}.py"
    pid_file = tmp_path / f"action-{mode}.pids"
    _write_action_script(action_script)
    supervisor_path = Path(process_supervisor.__file__)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(supervisor_path),
        sys.executable,
        str(action_script),
        str(pid_file),
        mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return process, pid_file


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_returncode"),
    [("success", 0), ("failure", 23)],
)
async def test_supervisor_propagates_exit_and_reaps_detached_descendant(
    tmp_path: Path,
    mode: str,
    expected_returncode: int,
) -> None:
    process, pid_file = await _spawn_supervised_action(tmp_path, mode=mode)
    tracked_pids: tuple[int, ...] = ()
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)

        assert process.returncode == expected_returncode, stderr.decode()
        assert stdout == b""
        tracked_pids = tuple(int(pid) for pid in pid_file.read_text().split())
        _, detached_pid = tracked_pids
        assert not _process_is_running(detached_pid)
    finally:
        if not tracked_pids and pid_file.exists():
            tracked_pids = tuple(int(pid) for pid in pid_file.read_text().split())
        for pid in tracked_pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


@pytest.mark.anyio
async def test_supervisor_reaps_detached_descendant_before_cancellation_returns(
    tmp_path: Path,
) -> None:
    process, pid_file = await _spawn_supervised_action(tmp_path, mode="block")
    await _wait_for_file(pid_file)
    action_pid, detached_pid = (int(pid) for pid in pid_file.read_text().split())
    communication = asyncio.create_task(
        communicate_process_group(
            process,
            timeout=30,
            terminate=terminate_supervised_process,
        )
    )

    try:
        await asyncio.sleep(0)
        communication.cancel()
        await asyncio.sleep(0)
        communication.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(communication, timeout=5)
        assert not _process_is_running(action_pid)
        assert not _process_is_running(detached_pid)
    finally:
        for pid in (action_pid, detached_pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
