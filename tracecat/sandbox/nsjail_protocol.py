"""Shared host-side protocol for preparing and invoking nsjail."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
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
    """Captured output and status from one nsjail invocation."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    workload_started: bool
    nsjail_log: bytes
    nsjail_signaled: bool


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


@dataclass(slots=True)
class NsjailProcess:
    """A launched nsjail process and the executor-owned files it depends on."""

    process: asyncio.subprocess.Process
    config_path: Path
    launcher_path: Path | None
    log_file: BinaryIO
    workload_started_marker: bytes | None


async def start_nsjail(
    *,
    nsjail_path: Path,
    job_dir: Path,
    config_text: str,
    env: dict[str, str],
    workload_launcher_name: str | None = None,
    workload_launcher_script: str | None = None,
    workload_started_marker: bytes | None = None,
    stdin_pipe: bool = False,
) -> NsjailProcess:
    """Prepare and launch one nsjail process without waiting for it.

    Filesystem and process-launch failures occur before workload code can run,
    so this boundary normalizes them as typed platform infrastructure failures.
    ``stdin_pipe`` gives the caller the jail's stdin; otherwise it is inherited.
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
        process = await asyncio.create_subprocess_exec(
            str(nsjail_path),
            "--config",
            str(config_path),
            *env_args,
            stdin=asyncio.subprocess.PIPE if stdin_pipe else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env,
            pass_fds=(log_file.fileno(),),
            start_new_session=True,
        )
    except BaseException as error:
        if log_file is not None:
            log_file.close()
        _cleanup_executor_file(config_path)
        if launcher_path is not None:
            _cleanup_executor_file(launcher_path)
        if isinstance(error, OSError):
            raise SandboxInfrastructureError(
                "Sandbox host failed to prepare or launch nsjail"
            ) from error
        raise
    return NsjailProcess(
        process=process,
        config_path=config_path,
        launcher_path=launcher_path,
        log_file=log_file,
        workload_started_marker=workload_started_marker,
    )


async def finish_nsjail(
    handle: NsjailProcess,
    *,
    timeout_seconds: float,
    timeout_message: str,
    input: bytes | None = None,  # noqa: A002
) -> NsjailCompletedProcess:
    """Feed, await, and clean up a launched nsjail process.

    The process group is killed and the executor-owned files are removed on
    every exit, including timeouts and cancellation.
    """
    process = handle.process
    try:
        try:
            stdout, stderr = await communicate_process_group(
                process,
                input=input,
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            raise SandboxTimeoutError(timeout_message) from error
        workload_started = False
        marker = handle.workload_started_marker
        if marker is not None and marker in stderr:
            workload_started = True
            stderr = stderr.replace(marker, b"", 1)
        log_scan = await asyncio.to_thread(scan_nsjail_log, handle.log_file)
        return NsjailCompletedProcess(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            workload_started=workload_started,
            nsjail_log=log_scan.tail,
            nsjail_signaled=log_scan.nsjail_signaled,
        )
    except OSError as error:
        raise SandboxInfrastructureError(
            "Sandbox host failed to prepare or launch nsjail"
        ) from error
    finally:
        close_nsjail_files(handle)


def close_nsjail_files(handle: NsjailProcess) -> None:
    """Release the log descriptor and remove the executor-owned files."""
    handle.log_file.close()
    _cleanup_executor_file(handle.config_path)
    if handle.launcher_path is not None:
        _cleanup_executor_file(handle.launcher_path)


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
    """Prepare, launch, await, and clean up one nsjail process."""
    handle = await start_nsjail(
        nsjail_path=nsjail_path,
        job_dir=job_dir,
        config_text=config_text,
        env=env,
        workload_launcher_name=workload_launcher_name,
        workload_launcher_script=workload_launcher_script,
        workload_started_marker=workload_started_marker,
    )
    return await finish_nsjail(
        handle,
        timeout_seconds=timeout_seconds,
        timeout_message=timeout_message,
    )
