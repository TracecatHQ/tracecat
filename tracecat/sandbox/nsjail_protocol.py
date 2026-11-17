"""Shared host-side protocol for preparing and invoking nsjail."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from tracecat.sandbox.exceptions import (
    SandboxInfrastructureError,
    SandboxTimeoutError,
)
from tracecat.sandbox.utils import communicate_process_group


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


# NsJail logs this as it SIGKILLs the jail on SIGTERM/SIGINT. The workload can
# neither signal NsJail nor write to its private log.
_NSJAIL_FATAL_SIGNAL_LINE = re.compile(
    rb"\[I\]\[[^\]]+\] Server stops due to fatal signal \(\d+\) caught"
)
_NSJAIL_LOG_TAIL_BYTES = 1024 * 1024


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
) -> NsjailCompletedProcess:
    """Prepare, launch, await, and clean up one nsjail process.

    Filesystem and process-launch failures occur before workload code can run,
    so this boundary normalizes them as typed platform infrastructure failures.
    """
    config_path = job_dir / "nsjail.cfg"
    launcher_path: Path | None = None
    # An unlinked file outside the jail. NsJail marks every descriptor except
    # stdio close-on-exec before starting the workload, so only NsJail writes it.
    log_file = None
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
            str(nsjail_path),
            "--config",
            str(config_path),
            *env_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env,
            pass_fds=(log_file.fileno(),),
            start_new_session=True,
        )
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
        if log_file is not None:
            log_file.close()
        _cleanup_executor_file(config_path)
        if launcher_path is not None:
            _cleanup_executor_file(launcher_path)
