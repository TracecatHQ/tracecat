"""Sandbox teardown kills the whole jail and never waits unbounded."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest

from tracecat.sandbox import nsjail_protocol, utils
from tracecat.sandbox.cgroup import kill_cgroup
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxTimeoutError,
)
from tracecat.sandbox.executor import _WORKLOAD_DONE_MARKER, _WORKLOAD_STARTED_MARKER
from tracecat.sandbox.nsjail_protocol import invoke_nsjail

# Stands in for NsJail: forks a jailed process that leaves NsJail's process
# group, ignores SIGTERM and keeps the output pipes open, and lists it in the
# jail's cgroup like NsJail would. Then writes any given stderr (hex) and
# exits, or keeps running ("hang").
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
os.write(2, bytes.fromhex(os.environ.get("FAKE_NSJAIL_STDERR", "")))
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
@pytest.mark.parametrize("mode", ["exit", "hang"])
async def test_deferred_teardown_kills_jailed_process_that_outlived_nsjail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Invariant: teardown deferred past an early result also kills the jail.

    The result returns at the done marker; the finish task then kills every
    process in the jail's cgroup and completes within the grace plus the bound,
    so the job dir and leases it guards are released.
    """
    monkeypatch.setattr(nsjail_protocol, "TEARDOWN_TIMEOUT_SECONDS", 1.0)
    cgroup_mount = tmp_path / "cgroup"
    cgroup_mount.mkdir()
    try:
        completed = await invoke_nsjail(
            nsjail_path=_fake_nsjail(tmp_path),
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={
                "FAKE_NSJAIL_MODE": mode,
                "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
                "FAKE_NSJAIL_STDERR": (
                    _WORKLOAD_STARTED_MARKER + _WORKLOAD_DONE_MARKER
                ).hex(),
            },
            timeout_seconds=30,
            timeout_message="synthetic timeout",
            workload_started_marker=_WORKLOAD_STARTED_MARKER,
            workload_done_marker=_WORKLOAD_DONE_MARKER,
            done_exit_grace_seconds=0.2,
            cgroup_mount=cgroup_mount,
        )
        assert completed.finish is not None

        final = await asyncio.wait_for(completed.finish, timeout=3)

        assert final.returncode is not None
        await _wait_for_process_exit(int((tmp_path / "jailed.pid").read_text()))
        assert not (tmp_path / "nsjail.cfg").exists()
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


@pytest.mark.anyio
async def test_abandoned_drain_closes_the_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: giving up on the drain releases the pipes' descriptors."""
    monkeypatch.setattr(utils, "TEARDOWN_TIMEOUT_SECONDS", 0.2)
    process = await asyncio.create_subprocess_exec(
        _fake_nsjail(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env={
            "FAKE_NSJAIL_MODE": "exit",
            "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
        },
    )
    try:
        with pytest.raises(utils.ProcessTeardownError):
            await asyncio.wait_for(utils.communicate_process_group(process), 5)
        stdout = process.stdout
        assert stdout is not None
        for _ in range(100):
            if stdout.at_eof():
                break
            await asyncio.sleep(0.01)
        assert stdout.at_eof()
    finally:
        _kill_leftover(tmp_path)


async def _wait_for_eof(stream: asyncio.StreamReader | None) -> None:
    assert stream is not None
    for _ in range(100):
        if stream.at_eof():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Pipe is still open")


@pytest.mark.anyio
@pytest.mark.parametrize("abandon", ["timeout", "cancel"])
async def test_abandoned_communication_closes_the_pipes(
    tmp_path: Path,
    abandon: str,
) -> None:
    """Invariant: a timed-out or cancelled call also releases the pipes."""
    process = await asyncio.create_subprocess_exec(
        _fake_nsjail(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env={
            "FAKE_NSJAIL_MODE": "hang",
            "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
        },
    )
    try:
        if abandon == "timeout":
            with pytest.raises(TimeoutError):
                await utils.communicate_process_group(process, timeout=0.3)
        else:
            call = asyncio.create_task(utils.communicate_process_group(process))
            await asyncio.sleep(0.3)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(call, 5)
        await _wait_for_eof(process.stdout)
        await _wait_for_eof(process.stderr)
    finally:
        _kill_leftover(tmp_path)


@pytest.mark.anyio
async def test_teardown_failure_before_done_marker_stays_an_infrastructure_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a reap that gives up surfaces its own error, not a cancellation."""
    monkeypatch.setattr(nsjail_protocol, "TEARDOWN_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(utils, "TEARDOWN_TIMEOUT_SECONDS", 0.2)
    try:
        with pytest.raises(SandboxInfrastructureError) as exc_info:
            await asyncio.wait_for(
                invoke_nsjail(
                    nsjail_path=_fake_nsjail(tmp_path),
                    job_dir=tmp_path,
                    config_text="mode: ONCE",
                    env={
                        "FAKE_NSJAIL_MODE": "exit",
                        "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
                    },
                    timeout_seconds=30,
                    timeout_message="synthetic timeout",
                    workload_started_marker=_WORKLOAD_STARTED_MARKER,
                    workload_done_marker=_WORKLOAD_DONE_MARKER,
                    cgroup_mount=None,
                ),
                timeout=5,
            )
        assert isinstance(exc_info.value.__cause__, utils.ProcessTeardownError)
    finally:
        _kill_leftover(tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize("done_marker", [None, _WORKLOAD_DONE_MARKER])
async def test_failed_cgroup_kill_fails_the_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    done_marker: bytes | None,
) -> None:
    """Invariant: a jail that could not be killed is a sandbox failure, not a success.

    The pipes it may still hold are closed on both the inline and the action
    (done marker) teardown paths.
    """

    def fail_kill(path: Path) -> None:
        raise PermissionError(f"synthetic cgroup.kill failure for {path.name}")

    closed: list[int] = []
    close_pipes = utils.close_process_pipes

    def record_close(process: asyncio.subprocess.Process) -> None:
        closed.append(process.pid)
        close_pipes(process)

    monkeypatch.setattr(nsjail_protocol, "kill_cgroup", fail_kill)
    monkeypatch.setattr(nsjail_protocol, "close_process_pipes", record_close)
    monkeypatch.setattr(utils, "close_process_pipes", record_close)
    cgroup_mount = tmp_path / "cgroup"
    cgroup_mount.mkdir()
    try:
        with pytest.raises(SandboxInfrastructureError) as exc_info:
            await asyncio.wait_for(
                invoke_nsjail(
                    nsjail_path=_fake_nsjail(tmp_path),
                    job_dir=tmp_path,
                    config_text="mode: ONCE",
                    env={
                        "FAKE_NSJAIL_MODE": "exit",
                        "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
                    },
                    timeout_seconds=30,
                    timeout_message="synthetic timeout",
                    workload_started_marker=_WORKLOAD_STARTED_MARKER,
                    workload_done_marker=done_marker,
                    cgroup_mount=cgroup_mount,
                ),
                timeout=5,
            )
        cause = exc_info.value.__cause__
        assert isinstance(cause, utils.ProcessTeardownError)
        assert isinstance(cause.__cause__, PermissionError)
        assert closed
    finally:
        _kill_leftover(tmp_path)


@pytest.mark.anyio
async def test_cancellation_survives_a_failed_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a cancelled call stays cancelled even if its teardown fails.

    The teardown failure is kept as the cancellation's cause.
    """

    def fail_kill(path: Path) -> None:
        raise PermissionError(f"synthetic cgroup.kill failure for {path.name}")

    monkeypatch.setattr(nsjail_protocol, "kill_cgroup", fail_kill)
    cgroup_mount = tmp_path / "cgroup"
    cgroup_mount.mkdir()
    call = asyncio.create_task(
        invoke_nsjail(
            nsjail_path=_fake_nsjail(tmp_path),
            job_dir=tmp_path,
            config_text="mode: ONCE",
            env={
                "FAKE_NSJAIL_MODE": "hang",
                "FAKE_NSJAIL_PID_FILE": str(tmp_path / "jailed.pid"),
            },
            timeout_seconds=30,
            timeout_message="synthetic timeout",
            workload_started_marker=_WORKLOAD_STARTED_MARKER,
            workload_done_marker=_WORKLOAD_DONE_MARKER,
            cgroup_mount=cgroup_mount,
        )
    )
    try:
        for _ in range(500):
            if (tmp_path / "jailed.pid").exists():
                break
            await asyncio.sleep(0.01)
        call.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await asyncio.wait_for(call, timeout=5)
        assert isinstance(exc_info.value.__cause__, utils.ProcessTeardownError)
    finally:
        _kill_leftover(tmp_path)


def test_kill_cgroup_prefers_cgroup_kill(tmp_path: Path) -> None:
    (tmp_path / "cgroup.kill").write_text("0")

    kill_cgroup(tmp_path)

    assert (tmp_path / "cgroup.kill").read_text() == "1"


@pytest.mark.anyio
async def test_kill_cgroup_falls_back_to_listed_pids(tmp_path: Path) -> None:
    """Invariant: without cgroup.kill, every PID listed in the subtree is killed."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    jail = tmp_path / "NSJAIL.1"
    jail.mkdir()
    (jail / "cgroup.procs").write_text(f"{process.pid}\n")
    try:
        kill_cgroup(tmp_path)
        assert await asyncio.wait_for(process.wait(), 5) == -signal.SIGKILL
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def test_kill_cgroup_ignores_missing_cgroup(tmp_path: Path) -> None:
    kill_cgroup(tmp_path / "missing")
