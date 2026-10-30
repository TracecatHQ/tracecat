"""Sandbox teardown kills the whole jail and never waits unbounded."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest

from tracecat.sandbox import utils
from tracecat.sandbox.cgroup import kill_cgroup
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxTimeoutError,
)
from tracecat.sandbox.nsjail_protocol import invoke_nsjail

# Stands in for NsJail: forks a jailed process that leaves NsJail's process
# group, ignores SIGTERM and keeps the output pipes open, and lists it in the
# jail's cgroup like NsJail would. Then exits, or keeps running ("hang").
_FAKE_NSJAIL = """
import os
import signal
import sys
import time
from pathlib import Path

args = sys.argv[1:]
ready_r, ready_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(ready_w, b"x")
    while True:
        time.sleep(60)
os.read(ready_r, 1)
if "--cgroupv2_mount" in args:
    jail = Path(args[args.index("--cgroupv2_mount") + 1]) / "NSJAIL.1"
    jail.mkdir()
    (jail / "cgroup.procs").write_text(f"{pid}\\n")
Path(os.environ["FAKE_NSJAIL_PID_FILE"]).write_text(str(pid))
if os.environ["FAKE_NSJAIL_MODE"] == "hang":
    while True:
        time.sleep(60)
"""


def _process_is_running(pid: int) -> bool:
    """Return whether a process is alive, treating zombies as terminated."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        return stat_path.read_text().split()[2] != "Z"
    except FileNotFoundError:
        return sys.platform == "darwin"


async def _wait_for_process_exit(pid: int) -> None:
    for _ in range(200):
        if not _process_is_running(pid):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Process {pid} is still running")


def _fake_nsjail(tmp_path: Path) -> Path:
    path = tmp_path / "nsjail"
    path.write_text(f"#!{sys.executable}\n{_FAKE_NSJAIL}")
    path.chmod(0o700)
    return path


async def _invoke(
    tmp_path: Path, *, mode: str, cgroup_mount: Path | None, timeout_seconds: float
) -> None:
    await invoke_nsjail(
        nsjail_path=_fake_nsjail(tmp_path),
        job_dir=tmp_path,
        config_text="mode: ONCE",
        env={
            "FAKE_NSJAIL_MODE": mode,
            "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
        },
        timeout_seconds=timeout_seconds,
        timeout_message="synthetic timeout",
        cgroup_mount=cgroup_mount,
    )


def _kill_leftover(tmp_path: Path) -> None:
    pid_file = tmp_path / "jailed.pid"
    if pid_file.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [("exit", None), ("hang", SandboxTimeoutError)],
)
async def test_teardown_kills_jailed_process_that_outlived_nsjail(
    tmp_path: Path,
    mode: str,
    expected_error: type[Exception] | None,
) -> None:
    """Invariant: teardown kills every process in the jail's cgroup.

    A jailed process that ignores SIGTERM, sits outside NsJail's process group
    and holds the output pipes is still killed, so the call returns promptly
    after NsJail exits or times out.
    """
    cgroup_mount = tmp_path / "cgroup"
    cgroup_mount.mkdir()
    try:
        call = _invoke(
            tmp_path, mode=mode, cgroup_mount=cgroup_mount, timeout_seconds=1
        )
        if expected_error is None:
            await asyncio.wait_for(call, timeout=utils.TEARDOWN_TIMEOUT_SECONDS)
        else:
            with pytest.raises(expected_error):
                await asyncio.wait_for(call, timeout=utils.TEARDOWN_TIMEOUT_SECONDS)

        await _wait_for_process_exit(int((tmp_path / "jailed.pid").read_text()))
        (jail_cgroup,) = cgroup_mount.iterdir()
        assert jail_cgroup.name.startswith("tracecat-")
    finally:
        _kill_leftover(tmp_path)


@pytest.mark.anyio
async def test_teardown_wait_is_bounded_without_a_cgroup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: pipes held past the teardown bound fail the call, not hang it."""
    monkeypatch.setattr(utils, "TEARDOWN_TIMEOUT_SECONDS", 0.2)
    try:
        with pytest.raises(SandboxInfrastructureError) as exc_info:
            await asyncio.wait_for(
                _invoke(tmp_path, mode="exit", cgroup_mount=None, timeout_seconds=30),
                timeout=5,
            )
        assert isinstance(exc_info.value.__cause__, utils.ProcessTeardownError)
    finally:
        _kill_leftover(tmp_path)


def test_kill_cgroup_prefers_cgroup_kill(tmp_path: Path) -> None:
    (tmp_path / "cgroup.kill").write_text("0")

    kill_cgroup(tmp_path)

    assert (tmp_path / "cgroup.kill").read_text() == "1"


def test_kill_cgroup_ignores_missing_cgroup(tmp_path: Path) -> None:
    kill_cgroup(tmp_path / "missing")
