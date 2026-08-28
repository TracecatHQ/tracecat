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


def _cleanup_config(config_path: Path) -> None:
    """Best-effort removal of executor-owned launch configuration."""
    try:
        config_path.unlink(missing_ok=True)
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
) -> NsjailCompletedProcess:
    """Prepare, launch, await, and clean up one nsjail process.

    Filesystem and process-launch failures occur before workload code can run,
    so this boundary normalizes them as typed platform infrastructure failures.
    """
    config_path = job_dir / "nsjail.cfg"
    try:
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
        return NsjailCompletedProcess(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    except OSError as error:
        raise SandboxInfrastructureError(
            "Sandbox host failed to prepare or launch nsjail"
        ) from error
    finally:
        _cleanup_config(config_path)
