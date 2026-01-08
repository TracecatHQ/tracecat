"""NSJail spawning utilities for agent runtime.

Provides utilities for the orchestrator to spawn AgentRuntime implementations
inside an NSJail sandbox. The orchestrator is responsible for:
- Creating Unix sockets
- Starting the trusted MCP server
- Managing the runtime lifecycle

This module handles:
- Generating nsjail config
- Spawning the sandboxed process

Uses the same rootfs as the action sandbox (TRACECAT__SANDBOX_ROOTFS_PATH).
Site-packages are mounted read-only for Claude SDK and tracecat dependencies.

When TRACECAT__DISABLE_NSJAIL=true, the runtime is spawned as a direct
subprocess instead of through nsjail. This is useful for:
- Testing on platforms without nsjail (macOS, Windows, CI)
- Development workflows
- Integration tests
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from tracecat.agent.sandbox.config import (
    AgentSandboxConfig,
    build_agent_env_map,
    build_agent_nsjail_config,
)
from tracecat.agent.sandbox.exceptions import (
    AgentSandboxExecutionError,
    AgentSandboxTimeoutError,
)
from tracecat.config import (
    TRACECAT__DISABLE_NSJAIL,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger


def _get_site_packages_dir() -> Path:
    """Get the site-packages directory containing tracecat dependencies."""
    # Find site-packages from the current interpreter
    for path in sys.path:
        if "site-packages" in path and Path(path).exists():
            return Path(path)

    # Fallback: derive from sys.prefix
    site_packages = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if site_packages.exists():
        return site_packages

    raise AgentSandboxExecutionError("Could not find site-packages directory")


async def spawn_jailed_runtime(
    socket_dir: Path,
    config: AgentSandboxConfig | None = None,
    nsjail_path: str = TRACECAT__SANDBOX_NSJAIL_PATH,
    rootfs_path: str = TRACECAT__SANDBOX_ROOTFS_PATH,
) -> asyncio.subprocess.Process:
    """Spawn the agent runtime inside an NSJail sandbox (or direct subprocess for testing).

    This is the entrypoint for the orchestrator to spawn a jailed runtime.
    The orchestrator is responsible for:
    - Creating socket_dir with control.sock and mcp.sock
    - Starting the trusted MCP HTTP server on socket_dir/mcp.sock
    - Sending RuntimeInitPayload after the runtime connects to control.sock
    - Reading events from the control socket

    When TRACECAT__DISABLE_NSJAIL=true, the runtime is spawned as a direct
    subprocess instead of through nsjail. This enables testing on platforms
    without nsjail (macOS, Windows, CI environments).

    Args:
        socket_dir: Directory containing Unix sockets (control.sock, mcp.sock).
            Mounted at /var/run/tracecat in the sandbox.
        config: Optional sandbox configuration. Defaults to standard agent config.
        nsjail_path: Path to the nsjail binary.
        rootfs_path: Path to the sandbox rootfs (same rootfs as action sandbox).

    Returns:
        The spawned subprocess. Caller is responsible for managing lifecycle.

    Raises:
        AgentSandboxExecutionError: If process fails to spawn.

    Example:
        ```python
        # Orchestrator usage:
        socket_dir = Path("/tmp/agent-job-xxx")
        socket_dir.mkdir(parents=True)

        # Start trusted MCP server on socket_dir / "mcp.sock"
        # Create control socket at socket_dir / "control.sock"

        process = await spawn_jailed_runtime(socket_dir=socket_dir)

        # Wait for runtime to connect to control socket
        # Send RuntimeInitPayload
        # Stream events until done
        # Cleanup
        ```
    """
    if config is None:
        config = AgentSandboxConfig()

    if not socket_dir.exists():
        raise AgentSandboxExecutionError(f"Socket directory not found: {socket_dir}")

    # Direct subprocess mode for testing (no nsjail)
    if TRACECAT__DISABLE_NSJAIL:
        return await _spawn_direct_runtime(socket_dir)

    # NSJail mode for production
    return await _spawn_nsjail_runtime(
        socket_dir=socket_dir,
        config=config,
        nsjail_path=nsjail_path,
        rootfs_path=rootfs_path,
    )


async def _spawn_direct_runtime(
    socket_dir: Path,
) -> asyncio.subprocess.Process:
    """Spawn the agent runtime as a direct subprocess (for development/testing).

    This bypasses nsjail and runs ClaudeAgentRuntime directly in the current
    Python environment. Used when TRACECAT__DISABLE_NSJAIL=true.

    WARNING: This mode has no isolation and should only be used for development/testing.
    """
    control_socket_path = socket_dir / "control.sock"

    cmd = [
        sys.executable,
        "-m",
        "tracecat.agent.sandbox.entrypoint",
        "--socket",
        str(control_socket_path),
    ]

    logger.info(
        "Spawning agent runtime (direct subprocess - DEVELOPMENT MODE)",
        socket_dir=str(socket_dir),
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )

    return process


async def _spawn_nsjail_runtime(
    socket_dir: Path,
    config: AgentSandboxConfig,
    nsjail_path: str,
    rootfs_path: str,
) -> asyncio.subprocess.Process:
    """Spawn the agent runtime inside an NSJail sandbox (production mode).

    The runtime uses tracecat.agent.sandbox.entrypoint which is available via
    site-packages mounted read-only. This provides isolation while using the
    unified ClaudeAgentRuntime (with lazy imports for minimal cold start).
    """
    rootfs = Path(rootfs_path)
    nsjail = Path(nsjail_path)

    # Validate paths
    if not rootfs.exists():
        raise AgentSandboxExecutionError(f"Rootfs not found: {rootfs}")
    if not nsjail.exists():
        raise AgentSandboxExecutionError(f"nsjail binary not found: {nsjail}")

    # Get site-packages directory
    site_packages_dir = _get_site_packages_dir()

    # Create temp directory for nsjail job
    job_id = uuid.uuid4().hex[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"agent-nsjail-{job_id}-"))

    try:
        # Build nsjail config
        nsjail_config = build_agent_nsjail_config(
            rootfs=rootfs,
            job_dir=job_dir,
            socket_dir=socket_dir,
            config=config,
            site_packages_dir=site_packages_dir,
        )

        # Write config to job directory
        config_path = job_dir / "nsjail.cfg"
        config_path.write_text(nsjail_config)
        config_path.chmod(0o600)

        # Build environment
        env_map = build_agent_env_map(config)
        env_args: list[str] = []
        for key, value in env_map.items():
            env_args.extend(["--env", f"{key}={value}"])

        # Build nsjail command
        cmd = [
            str(nsjail),
            "--config",
            str(config_path),
            *env_args,
        ]

        logger.info(
            "Spawning jailed agent runtime",
            job_dir=str(job_dir),
            socket_dir=str(socket_dir),
            site_packages=str(site_packages_dir),
        )

        # Spawn nsjail process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env_map,
        )

        return process

    except Exception as e:
        # Clean up job directory on spawn failure
        _cleanup_job_dir(job_dir)
        raise AgentSandboxExecutionError(f"Failed to spawn jailed runtime: {e}") from e


async def wait_for_process(
    process: asyncio.subprocess.Process,
    timeout_seconds: int,
) -> tuple[int, str, str]:
    """Wait for a jailed runtime process to complete.

    Args:
        process: The nsjail subprocess.
        timeout_seconds: Maximum time to wait.

    Returns:
        Tuple of (return_code, stdout, stderr).

    Raises:
        AgentSandboxTimeoutError: If process doesn't complete within timeout.
    """
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds + 10,  # Buffer for nsjail overhead
        )
    except TimeoutError as e:
        process.kill()
        await process.wait()
        raise AgentSandboxTimeoutError(
            f"Agent execution timed out after {timeout_seconds}s"
        ) from e

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

    return process.returncode or 0, stdout, stderr


def _cleanup_job_dir(job_dir: Path) -> None:
    """Clean up a job directory (best effort)."""
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as e:
        logger.warning("Failed to clean up job dir", job_dir=str(job_dir), error=str(e))
