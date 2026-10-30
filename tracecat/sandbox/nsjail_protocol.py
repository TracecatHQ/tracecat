"""Shared host-side protocol for preparing and invoking nsjail."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from tracecat.concurrency import rejoin_future_through_cancellation
from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxTimeoutError,
)
from tracecat.sandbox.utils import communicate_process_group, terminate_process_group


@dataclass(frozen=True, slots=True)
class NsjailCompletedProcess:
    """Captured output and status from one nsjail invocation.

    ``spawned_ns`` and ``exited_ns`` are wall-clock times taken just before the
    launch and once the host has collected the exited process, before the log
    scan. ``exited_ns`` therefore trails the real exit by the exit poll interval
    (10 ms) plus process-group cleanup and any event-loop delay.
    """

    returncode: int | None
    stdout: bytes
    stderr: bytes
    workload_started: bool
    nsjail_log: bytes
    nsjail_signaled: bool
    spawned_ns: int | None = None
    exited_ns: int | None = None
    finish: asyncio.Task[NsjailCompletedProcess] | None = None
    """Set when the workload reported done before NsJail exited.

    This record then holds the output so far and no NsJail log, ``returncode``
    is None and ``exited_ns`` is when the done marker arrived. The task waits
    for the exit, reaps the process group and cleans up, and returns the
    complete record. The caller must await it before releasing anything the
    jail still uses.
    """


# NsJail logs this as it SIGKILLs the jail on SIGTERM/SIGINT. The workload can
# neither signal NsJail nor write to its private log.
_NSJAIL_FATAL_SIGNAL_LINE = re.compile(
    rb"\[I\]\[[^\]]+\] Server stops due to fatal signal \(\d+\) caught"
)
_NSJAIL_LOG_TAIL_BYTES = 1024 * 1024
_PIPE_READ_CHUNK_BYTES = 64 * 1024
_EXIT_POLL_INTERVAL_SECONDS = 0.01
# After the done marker, a well-behaved jail exits within its teardown time
# (~120 ms with a TUN device). Past this, the process group is killed, so a
# forged early marker cannot keep a jail running outside the activity's slot.
DONE_EXIT_GRACE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class NsjailLogScan:
    """Records from a full NsJail log, plus a bounded tail."""

    tail: bytes
    nsjail_signaled: bool


def scan_nsjail_log(log: BinaryIO) -> NsjailLogScan:
    """Scan every NsJail log line, so noisy runs cannot hide a record."""
    log.seek(0)
    nsjail_signaled = any(_NSJAIL_FATAL_SIGNAL_LINE.match(line) for line in log)
    size = log.seek(0, os.SEEK_END)
    log.seek(max(0, size - _NSJAIL_LOG_TAIL_BYTES))
    return NsjailLogScan(tail=log.read(), nsjail_signaled=nsjail_signaled)


def _cleanup_executor_file(path: Path) -> None:
    """Best-effort removal of an executor-owned invocation file."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@dataclass(frozen=True, slots=True)
class _InvocationFiles:
    """Executor-owned files that live until NsJail has exited."""

    log_file: BinaryIO
    config_path: Path
    launcher_path: Path | None

    def cleanup(self) -> None:
        self.log_file.close()
        _cleanup_executor_file(self.config_path)
        if self.launcher_path is not None:
            _cleanup_executor_file(self.launcher_path)


def _strip_markers(stderr: bytes, *markers: bytes | None) -> tuple[bytes, list[bool]]:
    """Remove the first copy of each marker and report which were present."""
    seen: list[bool] = []
    for marker in markers:
        found = marker is not None and marker in stderr
        if found and marker is not None:
            stderr = stderr.replace(marker, b"", 1)
        seen.append(found)
    return stderr, seen


async def _pump(
    stream: asyncio.StreamReader | None,
    buffer: bytearray,
    marker: bytes | None = None,
    seen: asyncio.Event | None = None,
) -> None:
    """Read a pipe to EOF, setting ``seen`` once ``marker`` has arrived."""
    if stream is None:
        return
    while chunk := await stream.read(_PIPE_READ_CHUNK_BYTES):
        buffer.extend(chunk)
        if marker is None or seen is None or seen.is_set():
            continue
        if marker in buffer[-(len(chunk) + len(marker)) :]:
            seen.set()


async def _reap(process: asyncio.subprocess.Process, pumps: Awaitable[object]) -> None:
    """Kill the process group, then drain the pipes it held open."""
    await terminate_process_group(process)
    await pumps


async def _communicate_until_done(
    process: asyncio.subprocess.Process,
    *,
    files: _InvocationFiles,
    spawned_ns: int,
    timeout_seconds: float,
    workload_started_marker: bytes | None,
    workload_done_marker: bytes,
    done_exit_grace_seconds: float,
) -> NsjailCompletedProcess:
    """Wait for NsJail to exit or for the workload's done marker, whichever is first.

    Only the action path uses this: its runner writes the marker after the result
    file is complete, so the host can read the result while NsJail tears the jail
    down. Every other phase waits for the exit through communicate_process_group.
    """
    stdout = bytearray()
    stderr = bytearray()
    done = asyncio.Event()
    pumps = asyncio.gather(
        _pump(process.stdout, stdout),
        _pump(process.stderr, stderr, workload_done_marker, done),
    )
    done_waiter = asyncio.ensure_future(done.wait())
    try:
        async with asyncio.timeout(timeout_seconds):
            while process.returncode is None and not done.is_set():
                await asyncio.wait({done_waiter}, timeout=_EXIT_POLL_INTERVAL_SECONDS)
        if process.returncode is not None:
            await _reap(process, pumps)
    except BaseException:
        reaper = asyncio.ensure_future(_reap(process, pumps))
        await rejoin_future_through_cancellation(reaper)
        raise
    finally:
        done_waiter.cancel()

    async def scan_log() -> NsjailLogScan:
        return await asyncio.to_thread(scan_nsjail_log, files.log_file)

    def record(
        returncode: int | None, exited_ns: int, log_scan: NsjailLogScan
    ) -> NsjailCompletedProcess:
        text, (started, _) = _strip_markers(
            bytes(stderr), workload_started_marker, workload_done_marker
        )
        return NsjailCompletedProcess(
            returncode=returncode,
            stdout=bytes(stdout),
            stderr=text,
            workload_started=started,
            nsjail_log=log_scan.tail,
            nsjail_signaled=log_scan.nsjail_signaled,
            spawned_ns=spawned_ns,
            exited_ns=exited_ns,
        )

    if process.returncode is not None:
        return record(process.returncode, time.time_ns(), await scan_log())

    async def finish() -> NsjailCompletedProcess:
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + done_exit_grace_seconds
            while process.returncode is None and loop.time() < deadline:
                await asyncio.sleep(_EXIT_POLL_INTERVAL_SECONDS)
            exited_ns = time.time_ns()
            await _reap(process, pumps)
            return record(process.returncode, exited_ns, await scan_log())
        except BaseException:
            reaper = asyncio.ensure_future(_reap(process, pumps))
            await rejoin_future_through_cancellation(reaper)
            raise
        finally:
            files.cleanup()

    # Hand the running jail to its finish task with no await in between, so a
    # cancellation cannot leave it unowned. Only a valid result returns early,
    # and nothing reads the NsJail log for one.
    no_log = NsjailLogScan(tail=b"", nsjail_signaled=False)
    partial = record(None, time.time_ns(), no_log)
    return dataclasses.replace(partial, finish=asyncio.create_task(finish()))


async def invoke_nsjail(
    *,
    nsjail_path: Path,
    job_dir: Path,
    config_text: str,
    env: dict[str, str],
    timeout_seconds: float,
    timeout_message: str,
    workload_launcher_name: str | None = None,
    workload_launcher_script: str | None = None,
    workload_started_marker: bytes | None = None,
    workload_done_marker: bytes | None = None,
    done_exit_grace_seconds: float = DONE_EXIT_GRACE_SECONDS,
    command_prefix: Sequence[str] = (),
    extra_pass_fds: Sequence[int] = (),
) -> NsjailCompletedProcess:
    """Prepare, launch, await, and clean up one nsjail process.

    Filesystem and process-launch failures occur before workload code can run,
    so this boundary normalizes them as typed platform infrastructure failures.

    ``command_prefix`` runs nsjail through a launcher such as nsenter, which
    receives ``extra_pass_fds``. NsJail marks every inherited descriptor except
    stdio close-on-exec, so none reaches the workload.

    With ``workload_done_marker``, the call may return when the workload writes
    that marker, before NsJail exits; see ``NsjailCompletedProcess.finish``.
    """
    config_path = job_dir / "nsjail.cfg"
    launcher_path: Path | None = None
    # An unlinked file outside the jail. NsJail marks every descriptor except
    # stdio close-on-exec before starting the workload, so only NsJail writes it.
    log_file = None
    # Cleared when a finish task takes over the invocation files.
    owns_files = True
    try:
        log_file = tempfile.TemporaryFile()
        if workload_launcher_name is not None:
            if workload_launcher_script is None or workload_started_marker is None:
                raise ValueError(
                    "A workload launcher requires its script and start marker"
                )
            launcher_path = job_dir / workload_launcher_name
            _cleanup_executor_file(launcher_path)
            launcher_path.write_text(workload_launcher_script)
            launcher_path.chmod(0o600)

        # Pin INFO: the fatal-signal record that attributes drain kills is LOG_I.
        config_path.write_text(
            f"{config_text}\nlog_fd: {log_file.fileno()}\nlog_level: INFO\n"
        )
        config_path.chmod(0o600)
        env_args = [arg for key in env for arg in ("--env", key)]
        spawned_ns = time.time_ns()
        process = await asyncio.create_subprocess_exec(
            *command_prefix,
            str(nsjail_path),
            "--config",
            str(config_path),
            *env_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env,
            pass_fds=(log_file.fileno(), *extra_pass_fds),
            start_new_session=True,
        )
        if workload_done_marker is not None:
            try:
                completed = await _communicate_until_done(
                    process,
                    files=_InvocationFiles(log_file, config_path, launcher_path),
                    spawned_ns=spawned_ns,
                    timeout_seconds=timeout_seconds,
                    workload_started_marker=workload_started_marker,
                    workload_done_marker=workload_done_marker,
                    done_exit_grace_seconds=done_exit_grace_seconds,
                )
            except TimeoutError as error:
                raise SandboxTimeoutError(timeout_message) from error
            owns_files = completed.finish is None
            return completed
        try:
            stdout, stderr = await communicate_process_group(
                process,
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            raise SandboxTimeoutError(timeout_message) from error
        exited_ns = time.time_ns()
        workload_started = False
        if workload_started_marker is not None and workload_started_marker in stderr:
            workload_started = True
            stderr = stderr.replace(workload_started_marker, b"", 1)
        log_scan = await asyncio.to_thread(scan_nsjail_log, log_file)
        return NsjailCompletedProcess(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            workload_started=workload_started,
            nsjail_log=log_scan.tail,
            nsjail_signaled=log_scan.nsjail_signaled,
            spawned_ns=spawned_ns,
            exited_ns=exited_ns,
        )
    except OSError as error:
        raise SandboxInfrastructureError(
            "Sandbox host failed to prepare or launch nsjail"
        ) from error
    finally:
        if owns_files:
            if log_file is not None:
                log_file.close()
            _cleanup_executor_file(config_path)
            if launcher_path is not None:
                _cleanup_executor_file(launcher_path)
