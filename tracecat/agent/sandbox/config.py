"""NSJail configuration generation for agent runtime sandbox.

This module generates protobuf-format nsjail configurations specifically
for running the ClaudeAgentRuntime in an isolated sandbox.

Key differences from Python sandbox (tracecat/sandbox/executor.py):
- Network enabled (for LiteLLM gateway access)
- Long-running process with socket communication
- Claude SDK session storage mount
- Entry point is the agent runtime module
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from tracecat.agent.sandbox.exceptions import AgentSandboxValidationError

# Valid environment variable name pattern (POSIX compliant)
_ENV_VAR_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AgentResourceLimits:
    """Resource limits for agent sandbox execution.

    Attributes:
        memory_mb: Maximum memory in megabytes (default 4096 = 4 GiB).
        cpu_seconds: Maximum CPU time in seconds.
        max_file_size_mb: Maximum file size in megabytes.
        max_open_files: Maximum number of open file descriptors.
        max_processes: Maximum number of processes/threads.
        timeout_seconds: Maximum wall-clock execution time.
    """

    memory_mb: int = 4096
    cpu_seconds: int = 600
    max_file_size_mb: int = 256
    max_open_files: int = 512
    max_processes: int = 128
    timeout_seconds: int = 600


@dataclass
class AgentSandboxConfig:
    """Configuration for agent sandbox execution.

    Attributes:
        resources: Resource limits for the sandbox.
        env_vars: Environment variables to inject into the sandbox.
        allowed_network_hosts: Set of allowed network destinations (host:port format).
            Network namespace isolation is always enabled. Only these specific
            endpoints will be reachable (e.g., {"litellm-gateway:8080"}).
            Empty set means no network access.
    """

    resources: AgentResourceLimits = field(default_factory=AgentResourceLimits)
    env_vars: dict[str, str] = field(default_factory=dict)
    allowed_network_hosts: set[str] = field(default_factory=set)


# Minimal base environment for sandboxed agent processes
AGENT_SANDBOX_BASE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/home/agent",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


def _validate_env_key(key: str) -> None:
    """Validate environment variable key is safe for protobuf config.

    Args:
        key: Environment variable name to validate.

    Raises:
        AgentSandboxValidationError: If key contains invalid characters.
    """
    if not _ENV_VAR_KEY_PATTERN.match(key):
        raise AgentSandboxValidationError(
            f"Invalid environment variable key: {key!r}. "
            "Keys must match pattern [A-Za-z_][A-Za-z0-9_]*"
        )


def _validate_path(path: Path, name: str) -> None:
    """Validate path is safe for protobuf config interpolation.

    Args:
        path: Path to validate.
        name: Human-readable name for error messages.

    Raises:
        AgentSandboxValidationError: If path contains dangerous characters.
    """
    path_str = str(path)
    # Characters that could break protobuf text format parsing
    dangerous_chars = {'"', "'", "\n", "\r", "\\", "{", "}"}
    found_chars = [c for c in dangerous_chars if c in path_str]
    if found_chars:
        raise AgentSandboxValidationError(
            f"Invalid {name} path: contains dangerous characters {found_chars!r}"
        )


# Entrypoint module invoked by nsjail
ENTRYPOINT_MODULE = "tracecat.agent.sandbox.entrypoint"


def build_agent_nsjail_config(
    rootfs: Path,
    socket_dir: Path,
    control_socket_name: str,
    config: AgentSandboxConfig,
) -> str:
    """Build nsjail protobuf config for agent runtime execution.

    Args:
        rootfs: Path to the sandbox rootfs.
        socket_dir: Directory containing Unix sockets (will be bind-mounted).
        control_socket_name: Name of the control socket file in socket_dir.
        config: Agent sandbox configuration.

    Returns:
        nsjail protobuf configuration as a string.

    Raises:
        AgentSandboxValidationError: If any input contains dangerous characters.
    """
    # Validate inputs to prevent injection into protobuf config
    _validate_path(rootfs, "rootfs")
    _validate_path(socket_dir, "socket_dir")

    lines = [
        'name: "agent_sandbox"',
        "mode: ONCE",
        'hostname: "agent"',
        "keep_env: false",
        "",
        "# Namespace isolation - network namespace always enabled for security",
        "clone_newnet: true",
        "clone_newuser: true",
        "clone_newns: true",
        "clone_newpid: true",
        "clone_newipc: true",
        "clone_newuts: true",
        "",
        "# UID/GID mapping - map container user to current user",
        f'uidmap {{ inside_id: "1000" outside_id: "{os.getuid()}" count: 1 }}',
        f'gidmap {{ inside_id: "1000" outside_id: "{os.getgid()}" count: 1 }}',
        "",
        "# Rootfs mounts - read-only base system",
        f'mount {{ src: "{rootfs}/usr" dst: "/usr" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/lib" dst: "/lib" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/bin" dst: "/bin" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/etc" dst: "/etc" is_bind: true rw: false }}',
    ]

    # Optional mounts - only include if the directories exist in rootfs
    lib64_path = rootfs / "lib64"
    if lib64_path.exists():
        lines.append(
            f'mount {{ src: "{lib64_path}" dst: "/lib64" is_bind: true rw: false }}'
        )

    sbin_path = rootfs / "sbin"
    if sbin_path.exists():
        lines.append(
            f'mount {{ src: "{sbin_path}" dst: "/sbin" is_bind: true rw: false }}'
        )

    lines.extend(
        [
            "",
            "# /dev essentials",
            'mount { src: "/dev/null" dst: "/dev/null" is_bind: true rw: true }',
            'mount { src: "/dev/urandom" dst: "/dev/urandom" is_bind: true rw: false }',
            'mount { src: "/dev/random" dst: "/dev/random" is_bind: true rw: false }',
            'mount { src: "/dev/zero" dst: "/dev/zero" is_bind: true rw: false }',
            "",
            "# Temporary filesystems",
            'mount { dst: "/tmp" fstype: "tmpfs" rw: true options: "size=256M" }',
            "",
            "# Socket directory - for orchestrator communication",
            f'mount {{ src: "{socket_dir}" dst: "/sockets" is_bind: true rw: true }}',
            "",
            "# Agent home directory with Claude SDK session storage",
            'mount { dst: "/home/agent" fstype: "tmpfs" rw: true options: "size=128M" }',
        ]
    )

    # Resource limits
    lines.extend(
        [
            "",
            "# Resource limits",
            f"rlimit_as: {config.resources.memory_mb * 1024 * 1024}",
            f"rlimit_cpu: {config.resources.cpu_seconds}",
            f"rlimit_fsize: {config.resources.max_file_size_mb * 1024 * 1024}",
            f"rlimit_nofile: {config.resources.max_open_files}",
            f"rlimit_nproc: {config.resources.max_processes}",
            f"time_limit: {config.resources.timeout_seconds}",
        ]
    )

    # Execution settings - agent runtime entry point
    socket_path = f"/sockets/{control_socket_name}"
    lines.extend(
        [
            "",
            "# Execution - agent runtime entry point",
            'cwd: "/home/agent"',
            f'exec_bin {{ path: "/usr/local/bin/python3" arg: "-m" arg: "{ENTRYPOINT_MODULE}" arg: "--socket" arg: "{socket_path}" }}',
        ]
    )

    return "\n".join(lines)


def build_agent_env_map(config: AgentSandboxConfig) -> dict[str, str]:
    """Construct a sanitized environment for the nsjail agent process.

    Args:
        config: Agent sandbox configuration.

    Returns:
        Dictionary of environment variables to pass to nsjail.

    Raises:
        AgentSandboxValidationError: If any env var key is invalid.
    """
    env_map: dict[str, str] = {**AGENT_SANDBOX_BASE_ENV}

    for key, value in config.env_vars.items():
        _validate_env_key(key)
        env_map[key] = value

    return env_map
