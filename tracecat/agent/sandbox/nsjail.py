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
from dataclasses import dataclass
from pathlib import Path

from tracecat.agent.common.config import (
    CONTROL_SOCKET_NAME,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.exceptions import (
    AgentSandboxExecutionError,
    AgentSandboxTimeoutError,
)
from tracecat.agent.sandbox.config import (
    AgentSandboxConfig,
    build_agent_env_map,
    build_agent_nsjail_config,
)
from tracecat.config import (
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)
from tracecat.logger import logger


@dataclass(frozen=True)
class SpawnedRuntime:
    """Result from spawning an agent runtime subprocess.

    Contains the subprocess and optional job directory for cleanup.
    Works for both nsjail (sandboxed) and direct (development) modes.
    Callers should call cleanup_spawned_runtime() after the process completes.
    """

    process: asyncio.subprocess.Process
    """The spawned subprocess."""

    job_dir: Path | None
    """Temp directory for nsjail job (None in direct subprocess mode)."""


def cleanup_spawned_runtime(result: SpawnedRuntime) -> None:
    """Clean up resources from a spawned runtime after the process completes.

    Safe to call multiple times. Best effort - logs warnings on failure.

    Args:
        result: The SpawnedRuntime from spawn_jailed_runtime().
    """
    if result.job_dir is not None:
        _cleanup_job_dir(result.job_dir)


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


def _get_tracecat_pkg_dir() -> Path:
    """Get the tracecat package directory.

    This finds the tracecat package directory itself, which is typically
    /app/tracecat in Docker or {project_root}/tracecat in development.
    """
    import tracecat

    # tracecat.__file__ is /app/tracecat/__init__.py, we want /app/tracecat
    return Path(tracecat.__file__).parent


async def spawn_jailed_runtime(
    socket_dir: Path,
    llm_socket_path: Path | None = None,
    config: AgentSandboxConfig | None = None,
    nsjail_path: str = TRACECAT__SANDBOX_NSJAIL_PATH,
    rootfs_path: str = TRACECAT__SANDBOX_ROOTFS_PATH,
    *,
    enable_internet_access: bool = False,
) -> SpawnedRuntime:
    """Spawn the agent runtime inside an NSJail sandbox (or direct subprocess for testing).

    This is the entrypoint for the orchestrator to spawn a jailed runtime.
    The orchestrator is responsible for:
    - Creating socket_dir with control.sock
    - Starting the LLM socket proxy on llm_socket_path
    - Starting the trusted MCP HTTP server on socket_dir/mcp.sock
    - Sending RuntimeInitPayload after the runtime connects to control.sock
    - Reading events from the control socket

    When TRACECAT__DISABLE_NSJAIL=true, the runtime is spawned as a direct
    subprocess instead of through nsjail. This enables testing on platforms
    without nsjail (macOS, Windows, CI environments).

    Args:
        socket_dir: Directory containing the per-job control socket (control.sock).
        llm_socket_path: Path to the LLM socket for proxied LiteLLM access.
            Required in production mode (NSJail), optional in direct mode.
        config: Optional sandbox configuration. Defaults to standard agent config.
        nsjail_path: Path to the nsjail binary.
        rootfs_path: Path to the sandbox rootfs (same rootfs as action sandbox).

    Returns:
        SpawnedRuntime containing the subprocess and job directory.
        Caller is responsible for managing lifecycle and calling
        cleanup_spawned_runtime() after the process completes.

    Raises:
        AgentSandboxExecutionError: If process fails to spawn.

    Example:
        ```python
        # Orchestrator usage:
        socket_dir = Path("/tmp/agent-job-xxx")
        socket_dir.mkdir(parents=True)

        # Start LLM socket proxy on socket_dir / "llm.sock"
        # Start trusted MCP server on socket_dir / "mcp.sock"
        # Create control socket at socket_dir / "control.sock"

        result = await spawn_jailed_runtime(
            socket_dir=socket_dir,
            llm_socket_path=socket_dir / "llm.sock",
        )
        try:
            # Wait for runtime to connect to control socket
            # Send RuntimeInitPayload
            # Stream events until done
            await result.process.wait()
        finally:
            cleanup_spawned_runtime(result)
        ```
    """
    if config is None:
        config = AgentSandboxConfig()

    if not socket_dir.exists():
        raise AgentSandboxExecutionError(f"Socket directory not found: {socket_dir}")

    # Direct subprocess mode for testing (no nsjail)
    if TRACECAT__DISABLE_NSJAIL:
        return await _spawn_direct_runtime(
            socket_dir=socket_dir,
            llm_socket_path=llm_socket_path,
        )

    # NSJail mode for production - llm_socket_path is required
    if llm_socket_path is None:
        raise AgentSandboxExecutionError(
            "llm_socket_path is required in production mode (NSJail)"
        )

    # NSJail mode for production
    return await _spawn_nsjail_runtime(
        socket_dir=socket_dir,
        llm_socket_path=llm_socket_path,
        config=config,
        nsjail_path=nsjail_path,
        rootfs_path=rootfs_path,
        enable_internet_access=enable_internet_access,
    )


async def _spawn_direct_runtime(
    *,
    socket_dir: Path,
    llm_socket_path: Path | None,
) -> SpawnedRuntime:
    """Spawn the agent runtime as a direct subprocess (for development/testing).

    This bypasses nsjail and runs ClaudeAgentRuntime directly in the current
    Python environment. Used when TRACECAT__DISABLE_NSJAIL=true.

    Security: Uses minimal base environment to prevent host secrets from
    leaking into the subprocess. Only passes socket paths and essential
    Python configuration.
    """
    from tracecat.agent.sandbox.config import (
        AGENT_SANDBOX_BASE_ENV,
        JAILED_CONTROL_SOCKET_PATH,
        TRUSTED_MCP_SOCKET_PATH,
    )

    control_socket_path = socket_dir / CONTROL_SOCKET_NAME

    cmd = [
        sys.executable,
        "-m",
        "tracecat.agent.sandbox.entrypoint",
    ]

    logger.info(
        "Spawning agent runtime (direct subprocess - DEVELOPMENT MODE)",
        control_socket_path=str(JAILED_CONTROL_SOCKET_PATH),
        mcp_socket_path=str(TRUSTED_MCP_SOCKET_PATH),
    )

    # Use minimal base environment instead of inheriting full host env
    # This prevents secrets and sensitive env vars from leaking
    env = {
        **AGENT_SANDBOX_BASE_ENV,
        # Override for direct mode
        "TRACECAT__DISABLE_NSJAIL": "true",
        # Point the runtime at the orchestrator's per-job control socket
        "TRACECAT__AGENT_CONTROL_SOCKET_PATH": str(control_socket_path),
        # Use host's HOME for Claude SDK session storage
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if llm_socket_path is not None:
        # If the runtime uses LLMBridge (internet access disabled), it must connect
        # to the orchestrator-side LLM socket.
        env["TRACECAT__AGENT_LLM_SOCKET_PATH"] = str(llm_socket_path)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    return SpawnedRuntime(process=process, job_dir=None)


async def _spawn_nsjail_runtime(
    socket_dir: Path,
    llm_socket_path: Path,
    config: AgentSandboxConfig,
    nsjail_path: str,
    rootfs_path: str,
    *,
    enable_internet_access: bool = False,
) -> SpawnedRuntime:
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
    if not llm_socket_path.exists():
        raise AgentSandboxExecutionError(f"LLM socket not found: {llm_socket_path}")

    # Get site-packages and tracecat package directories
    site_packages_dir = _get_site_packages_dir()
    tracecat_pkg_dir = _get_tracecat_pkg_dir()

    # Create temp directory for nsjail job
    job_id = uuid.uuid4().hex[:12]
    job_dir = Path(tempfile.mkdtemp(prefix=f"agent-nsjail-{job_id}-"))

    try:
        # Build nsjail config (socket paths are derived from socket_dir internally)
        nsjail_config = build_agent_nsjail_config(
            rootfs=rootfs,
            job_dir=job_dir,
            socket_dir=socket_dir,
            config=config,
            site_packages_dir=site_packages_dir,
            tracecat_pkg_dir=tracecat_pkg_dir,
            llm_socket_path=llm_socket_path,
            enable_internet_access=enable_internet_access,
        )

        # Write config to job directory
        config_path = job_dir / "nsjail.cfg"
        await asyncio.to_thread(config_path.write_text, nsjail_config)
        await asyncio.to_thread(config_path.chmod, 0o600)

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
            tracecat_pkg=str(tracecat_pkg_dir),
        )

        # Spawn nsjail process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job_dir),
            env=env_map,
        )

        # Return result with job_dir for caller to clean up after process completes
        return SpawnedRuntime(process=process, job_dir=job_dir)

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
