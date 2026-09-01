"""Shared host-side protocol for preparing and invoking nsjail."""

from __future__ import annotations

import asyncio
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
    try:
        if workload_launcher_name is not None:
            if workload_launcher_script is None or workload_started_marker is None:
                raise ValueError(
                    "A workload launcher requires its script and start marker"
                )
            launcher_path = job_dir / workload_launcher_name
            _cleanup_executor_file(launcher_path)
            launcher_path.write_text(workload_launcher_script)
            launcher_path.chmod(0o600)

        config_path.write_text(config_text)
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
        return NsjailCompletedProcess(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            workload_started=workload_started,
        )
    except OSError as error:
        raise SandboxInfrastructureError(
            "Sandbox host failed to prepare or launch nsjail"
        ) from error
    finally:
        _cleanup_executor_file(config_path)
        if launcher_path is not None:
            _cleanup_executor_file(launcher_path)
