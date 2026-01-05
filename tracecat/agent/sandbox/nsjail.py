"""NSJail spawning utilities for agent runtime.

Provides utilities for the orchestrator to spawn AgentRuntime implementations
inside an NSJail sandbox. The orchestrator is responsible for:
- Creating Unix sockets
- Starting the trusted MCP server
- Managing the runtime lifecycle

This module only handles nsjail process spawning and configuration.
"""

from __future__ import annotations

import asyncio
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
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger


async def spawn_jailed_runtime(
    socket_dir: Path,
    control_socket_name: str,
    config: AgentSandboxConfig | None = None,
    nsjail_path: str = TRACECAT__SANDBOX_NSJAIL_PATH,
    rootfs_path: str = TRACECAT__SANDBOX_ROOTFS_PATH,
) -> asyncio.subprocess.Process:
    """Spawn the agent runtime inside an NSJail sandbox.

    This is the entrypoint for the orchestrator to spawn a jailed runtime.
    The orchestrator is responsible for:
    - Creating the socket_dir with control and MCP sockets
    - Starting the trusted MCP HTTP server
    - Sending RuntimeInitPayload after the runtime connects
    - Reading events from the control socket

    Args:
        socket_dir: Directory containing Unix sockets (will be bind-mounted to /sockets).
        control_socket_name: Name of the control socket file in socket_dir.
        config: Optional sandbox configuration. Defaults to standard agent config.
        nsjail_path: Path to the nsjail binary.
        rootfs_path: Path to the sandbox rootfs.

    Returns:
        The spawned nsjail subprocess. Caller is responsible for managing lifecycle.

    Raises:
        AgentSandboxExecutionError: If nsjail fails to spawn.

    Example:
        ```python
        # Orchestrator usage:
        socket_dir = Path("/tmp/agent-job-xxx/sockets")
        socket_dir.mkdir(parents=True)

        # Start trusted MCP server on socket_dir / "mcp.sock"
        # Start control server on socket_dir / "control.sock"

        process = await spawn_jailed_runtime(
            socket_dir=socket_dir,
            control_socket_name="control.sock",
        )

        # Wait for runtime to connect to control socket
        # Send RuntimeInitPayload
        # Stream events until done
        # Cleanup
        ```
    """
    if config is None:
        config = AgentSandboxConfig()

    rootfs = Path(rootfs_path)
    nsjail = Path(nsjail_path)

    # Validate paths
    if not rootfs.exists():
        raise AgentSandboxExecutionError(f"Rootfs not found: {rootfs}")
    if not nsjail.exists():
        raise AgentSandboxExecutionError(f"nsjail binary not found: {nsjail}")
    if not socket_dir.exists():
        raise AgentSandboxExecutionError(f"Socket directory not found: {socket_dir}")

    # Create temp directory for nsjail config
    job_id = uuid.uuid4().hex[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"agent-nsjail-{job_id}-"))

    try:
        # Build nsjail config
        nsjail_config = build_agent_nsjail_config(
            rootfs=rootfs,
            socket_dir=socket_dir,
            control_socket_name=control_socket_name,
            config=config,
        )

        # Write config to job directory
        config_path = job_dir / "nsjail.cfg"
        config_path.write_text(nsjail_config)
        config_path.chmod(0o600)

        # Build environment
        env_map = build_agent_env_map(config)
        env_args: list[str] = []
        for key in env_map:
            env_args.extend(["--env", key])

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
            control_socket=control_socket_name,
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
        for f in job_dir.iterdir():
            if f.is_file():
                f.unlink()
        job_dir.rmdir()
    except Exception as e:
        logger.warning("Failed to clean up job dir", job_dir=str(job_dir), error=str(e))
