"""Shared host-side protocol for preparing and invoking nsjail."""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

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
    nsjail_log: bytes = b""
    termination_signal: int | None = None
    time_limit_kill: bool = False


# NsJail returns 128+N both when its child exits with status 128+N and when a
# signal N kills it. Only its own log, which the jailed process cannot reach,
# records which one happened.
_NSJAIL_SIGNALED_LINE = re.compile(
    rb"pid=\d+ \([^)]*\) terminated with signal: \S+ \((\d+)\)"
)
_NSJAIL_EXITED_LINE = re.compile(rb"pid=\d+ \([^)]*\) exited with status: \d+")
_NSJAIL_TIME_LIMIT_LINE = re.compile(rb"run time >= time limit")
_NSJAIL_LOG_TAIL_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class NsjailLogScan:
    """Termination records from a full NsJail log, plus a bounded tail."""

    tail: bytes
    termination_signal: int | None
    time_limit_kill: bool


def scan_nsjail_log(lines: Iterable[bytes]) -> NsjailLogScan:
    """Scan every NsJail log line; the termination record is written last."""
    tail: deque[bytes] = deque()
    tail_bytes = 0
    termination_signal: int | None = None
    time_limit_kill = False
    for line in lines:
        if match := _NSJAIL_SIGNALED_LINE.search(line):
            termination_signal = int(match.group(1))
        elif _NSJAIL_EXITED_LINE.search(line):
            termination_signal = None
        if _NSJAIL_TIME_LIMIT_LINE.search(line):
            time_limit_kill = True
        kept = line[-_NSJAIL_LOG_TAIL_BYTES:]
        tail.append(kept)
        tail_bytes += len(kept)
        while tail_bytes > _NSJAIL_LOG_TAIL_BYTES:
            tail_bytes -= len(tail.popleft())
    return NsjailLogScan(
        tail=b"".join(tail),
        termination_signal=termination_signal,
        time_limit_kill=time_limit_kill,
    )


def nsjail_termination_signal(nsjail_log: bytes) -> int | None:
    """Return the signal that killed the jailed process, per NsJail's log."""
    return scan_nsjail_log(nsjail_log.splitlines(keepends=True)).termination_signal


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

        config_path.write_text(f"{config_text}\nlog_fd: {log_file.fileno()}\n")
        config_path.chmod(0o600)
        env_args = [arg for key in env for arg in ("--env", key)]
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
        workload_started = False
        if workload_started_marker is not None and workload_started_marker in stderr:
            workload_started = True
            stderr = stderr.replace(workload_started_marker, b"", 1)
        log_file.seek(0)
        log_scan = scan_nsjail_log(log_file)
        return NsjailCompletedProcess(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            workload_started=workload_started,
            nsjail_log=log_scan.tail,
            termination_signal=log_scan.termination_signal,
            time_limit_kill=log_scan.time_limit_kill,
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
