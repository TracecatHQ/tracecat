"""Contain descendants of one Linux subprocess.

The outer process remains the child observed by its caller. A detached
subreaper monitor owns the actual action process and all descendants orphaned by
it. Closing the control pipe asks that monitor to kill and reap its complete
child tree before the outer process exits.

This module intentionally uses only the Python standard library so invoking it
does not import the Tracecat application in an untrusted action process.
"""

from __future__ import annotations

import ctypes
import os
import select
import signal
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from types import FrameType

_PR_SET_CHILD_SUBREAPER = 36
_PR_SET_PDEATHSIG = 1
_PARENT_POLL_INTERVAL_MS = 10


def _exit_code(wait_status: int) -> int:
    """Convert a wait status into a shell-compatible non-negative exit code."""
    code = os.waitstatus_to_exitcode(wait_status)
    return code if code >= 0 else 128 - code


def _set_child_subreaper() -> None:
    """Make this process the reparenting boundary for orphaned descendants."""
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _set_parent_death_signal(signal_number: int) -> None:
    """Ask Linux to signal this process when its current parent exits."""
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_PDEATHSIG, signal_number, 0, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _direct_child_pids_from_children_file() -> list[int]:
    """Return direct child PIDs using procfs's optional children file."""
    children_path = Path(f"/proc/self/task/{os.getpid()}/children")
    contents = children_path.read_text().strip()
    return [int(pid) for pid in contents.split()] if contents else []


def _proc_parent_pid(stat_path: Path) -> int:
    """Read one process's parent PID from its procfs stat record."""
    contents = stat_path.read_text()
    closing_paren = contents.rfind(")")
    fields = contents[closing_paren + 1 :].split()
    if closing_paren < 0 or len(fields) < 2:
        raise RuntimeError("Malformed procfs stat record")
    return int(fields[1])


def _direct_child_pids_from_proc_stat() -> list[int]:
    """Return direct child PIDs by scanning portable procfs stat records."""
    parent_pid = os.getpid()
    _proc_parent_pid(Path("/proc/self/stat"))
    child_pids: list[int] = []

    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                candidate_pid = int(entry.name)
                candidate_parent_pid = _proc_parent_pid(Path(entry.path) / "stat")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if candidate_parent_pid == parent_pid:
                child_pids.append(candidate_pid)

    return child_pids


def _direct_child_pids() -> list[int]:
    """Return direct child PIDs from either available procfs interface."""
    try:
        return _direct_child_pids_from_children_file()
    except OSError:
        return _direct_child_pids_from_proc_stat()


def _waitpid(pid: int, options: int = 0) -> tuple[int, int]:
    """Wait for a child while tolerating signal interruptions."""
    while True:
        try:
            return os.waitpid(pid, options)
        except InterruptedError:
            continue


def _kill_and_reap_children() -> None:
    """Kill every adopted child, including descendants orphaned while reaping."""
    while child_pids := _direct_child_pids():
        for child_pid in child_pids:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
        for child_pid in child_pids:
            with suppress(ChildProcessError):
                _waitpid(child_pid)


def _exec(command: Sequence[str], control_fd: int) -> None:
    """Replace this child with the actual action command."""
    os.close(control_fd)
    try:
        os.execvpe(command[0], list(command), os.environ)
    except OSError:
        with suppress(OSError):
            os.write(2, b"Failed to execute supervised process\n")
        os._exit(127)


def _run_monitor(
    control_fd: int,
    command: Sequence[str],
    *,
    supervisor_pid: int,
) -> int:
    """Run the action below a detached subreaper and contain its descendants."""
    try:
        parent_cleanup_requested = False

        def request_parent_cleanup(
            _signal: int,
            _frame: FrameType | None,
        ) -> None:
            nonlocal parent_cleanup_requested
            parent_cleanup_requested = True

        # The action can stop this same-UID monitor and kill its outer parent.
        # SIGCONT both resumes the monitor and records that it must clean up.
        signal.signal(signal.SIGCONT, request_parent_cleanup)
        _set_parent_death_signal(signal.SIGCONT)
        # Close the fork-to-prctl race before any untrusted command is started.
        if os.getppid() != supervisor_pid or parent_cleanup_requested:
            return 128 + signal.SIGTERM

        os.setsid()
        _set_child_subreaper()
        _direct_child_pids()

        action_pid = os.fork()
        if action_pid == 0:
            _exec(command, control_fd)

        poller = select.poll()
        poller.register(control_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        action_status: int | None = None
        parent_closed = False

        while (
            action_status is None and not parent_closed and not parent_cleanup_requested
        ):
            waited_pid, wait_status = _waitpid(action_pid, os.WNOHANG)
            if waited_pid == action_pid:
                action_status = wait_status
                break
            if poller.poll(_PARENT_POLL_INTERVAL_MS):
                parent_closed = os.read(control_fd, 1) == b""

        _kill_and_reap_children()
        if parent_closed or parent_cleanup_requested:
            return 128 + signal.SIGTERM
        if action_status is None:
            raise RuntimeError("Supervised action exited without a wait status")
        return _exit_code(action_status)
    except BaseException:
        with suppress(BaseException):
            _kill_and_reap_children()
        with suppress(OSError):
            os.write(2, b"Process supervisor failed\n")
        return 1
    finally:
        with suppress(OSError):
            os.close(control_fd)


def supervise(command: Sequence[str]) -> int:
    """Run one command and return only after all of its descendants are reaped."""
    if sys.platform != "linux":
        raise RuntimeError("The process supervisor requires Linux")
    if not command:
        raise ValueError("A supervised command is required")

    # The monitor is intentionally visible as the action's parent. Make this
    # outer process a second subreaper so killing that monitor only reparents
    # the action tree here, where it is still killed and reaped before return.
    _set_child_subreaper()
    _direct_child_pids()

    control_read_fd, control_write_fd = os.pipe()
    writer_open = True
    monitor_pid: int | None = None

    def close_control_pipe() -> None:
        nonlocal writer_open
        if writer_open:
            with suppress(OSError):
                os.close(control_write_fd)
            writer_open = False

    def request_cleanup(_signal: int, _frame: FrameType | None) -> None:
        close_control_pipe()
        # The action shares the monitor's UID and can stop it. SIGKILL the
        # monitor session so the outer subreaper adopts and reaps every member,
        # including descendants that detached into their own sessions.
        if monitor_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(monitor_pid, signal.SIGKILL)

    signal.signal(signal.SIGTERM, request_cleanup)
    signal.signal(signal.SIGINT, request_cleanup)

    supervisor_pid = os.getpid()
    monitor_pid = os.fork()
    if monitor_pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.close(control_write_fd)
        os._exit(
            _run_monitor(
                control_read_fd,
                command,
                supervisor_pid=supervisor_pid,
            )
        )

    os.close(control_read_fd)
    try:
        _, monitor_status = _waitpid(monitor_pid)
        return _exit_code(monitor_status)
    finally:
        close_control_pipe()
        _kill_and_reap_children()


def main() -> int:
    """CLI entry point."""
    return supervise(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
