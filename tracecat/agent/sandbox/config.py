"""NSJail configuration generation for agent runtime sandbox.

This module generates protobuf-format nsjail configurations specifically
for running the agent runtime in an isolated sandbox.

Security model:
- Network enabled (clone_newnet: false) - direct internet access for API calls
- LLM access via internal bridge (localhost:4100) proxied through Unix socket to host LiteLLM
- Namespace isolation (PID, user, mount, IPC, UTS namespaces)
- /proc read-only, PID namespace isolated (process only sees itself)
- All tool execution via MCP socket to trusted server outside sandbox
- Uses same base rootfs as action sandbox (Python 3.12)
- Site-packages mounted read-only for Claude SDK deps and tracecat package

Key design:
- Runtime executed via `python -m tracecat.agent.sandbox.entrypoint`
- Mount site-packages read-only for deps (includes tracecat package)
- Control socket at /var/run/tracecat/control.sock
- LLM socket at /var/run/tracecat/llm.sock (proxied to LiteLLM)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from tracecat.agent.common.config import (
    TRACECAT__AGENT_SANDBOX_MEMORY_MB,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
)
from tracecat.agent.common.exceptions import AgentSandboxValidationError

# Well-known socket paths (internal to agent worker, not configurable)
TRUSTED_MCP_SOCKET_PATH = Path("/var/run/tracecat/mcp.sock")
CONTROL_SOCKET_NAME = "control.sock"
JAILED_CONTROL_SOCKET_PATH = Path("/var/run/tracecat/control.sock")

# LLM socket for proxied access to LiteLLM (network isolated)
LLM_SOCKET_NAME = "llm.sock"
JAILED_LLM_SOCKET_PATH = Path("/var/run/tracecat/llm.sock")

# Valid environment variable name pattern (POSIX compliant)
_ENV_VAR_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _contains_dangerous_chars(value: str) -> tuple[bool, str | None]:
    """Check for null bytes and control characters that could affect sandbox behavior.

    Null bytes can truncate strings in C-based tools like nsjail. Control characters
    could affect sandbox behavior in unexpected ways.

    Args:
        value: String value to check.

    Returns:
        Tuple of (is_dangerous, reason). If is_dangerous is False, reason is None.
    """
    if "\x00" in value:
        return True, "contains null byte"

    for char in value:
        code = ord(char)
        # Control chars 0x01-0x1F (except tab 0x09, newline 0x0A, CR 0x0D) and DEL 0x7F
        if code < 0x20 and code not in (0x09, 0x0A, 0x0D):
            return True, f"contains control character 0x{code:02x}"
        if code == 0x7F:
            return True, "contains DEL control character"

    return False, None


@dataclass(frozen=True)
class AgentResourceLimits:
    """Resource limits for agent sandbox execution.

    Defaults are read from environment variables:
    - TRACECAT__AGENT_SANDBOX_MEMORY_MB: memory_mb (default 4096 = 4 GiB)
    - TRACECAT__AGENT_SANDBOX_TIMEOUT: timeout_seconds and cpu_seconds (default 600s)

    Attributes:
        memory_mb: Maximum memory in megabytes.
        cpu_seconds: Maximum CPU time in seconds.
        max_file_size_mb: Maximum file size in megabytes.
        max_open_files: Maximum number of open file descriptors.
        max_processes: Maximum number of processes/threads.
        timeout_seconds: Maximum wall-clock execution time.
    """

    memory_mb: int = field(default_factory=lambda: TRACECAT__AGENT_SANDBOX_MEMORY_MB)
    cpu_seconds: int = field(default_factory=lambda: TRACECAT__AGENT_SANDBOX_TIMEOUT)
    max_file_size_mb: int = 256
    max_open_files: int = 512
    max_processes: int = 128
    timeout_seconds: int = field(
        default_factory=lambda: TRACECAT__AGENT_SANDBOX_TIMEOUT
    )


@dataclass
class AgentSandboxConfig:
    """Configuration for agent sandbox execution.

    Attributes:
        resources: Resource limits for the sandbox.
        env_vars: Environment variables to inject into the sandbox.
    """

    resources: AgentResourceLimits = field(default_factory=AgentResourceLimits)
    env_vars: dict[str, str] = field(default_factory=dict)


# Minimal base environment for sandboxed agent processes
AGENT_SANDBOX_BASE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/home/agent",
    "USER": "agent",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    # Required for Python to find libpython3.12.so in nsjail sandbox
    "LD_LIBRARY_PATH": "/usr/local/lib:/usr/lib:/lib",
    # PYTHONPATH: /app for tracecat package, /site-packages for dependencies
    "PYTHONPATH": "/app:/site-packages",
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


def _validate_env_value(key: str, value: str) -> None:
    """Validate environment variable value is safe for sandbox execution.

    Args:
        key: Environment variable name (for error messages).
        value: Environment variable value to validate.

    Raises:
        AgentSandboxValidationError: If value contains dangerous characters.
    """
    is_dangerous, reason = _contains_dangerous_chars(value)
    if is_dangerous:
        raise AgentSandboxValidationError(
            f"Invalid environment variable value for {key!r}: {reason}"
        )


def _validate_path(path: Path, name: str) -> None:
    """Validate path is safe for protobuf config interpolation.

    Args:
        path: Path to validate.
        name: Human-readable name for error messages.

    Raises:
        AgentSandboxValidationError: If path contains dangerous characters or traversal.
    """
    path_str = str(path)

    # Check for null bytes and control characters
    is_dangerous, reason = _contains_dangerous_chars(path_str)
    if is_dangerous:
        raise AgentSandboxValidationError(f"Invalid {name} path: {reason}")

    # Check for path traversal attempts
    # Resolve to absolute and check it doesn't escape expected boundaries
    try:
        path.resolve()
    except (OSError, ValueError) as e:
        raise AgentSandboxValidationError(
            f"Invalid {name} path: failed to resolve - {e}"
        ) from e

    # Characters that could break protobuf text format parsing
    dangerous_chars = {'"', "'", "\n", "\r", "\\", "{", "}"}
    found_chars = [c for c in dangerous_chars if c in path_str]
    if found_chars:
        raise AgentSandboxValidationError(
            f"Invalid {name} path: contains dangerous characters {found_chars!r}"
        )

    # Reject raw ".." components even before resolution (defense in depth)
    path_parts = path_str.split("/")
    if ".." in path_parts:
        raise AgentSandboxValidationError(
            f"Invalid {name} path: contains '..' path traversal component"
        )


def build_agent_nsjail_config(
    rootfs: Path,
    job_dir: Path,
    socket_dir: Path,
    config: AgentSandboxConfig,
    site_packages_dir: Path,
    tracecat_pkg_dir: Path,
    llm_socket_path: Path,
    *,
    enable_internet_access: bool = False,
) -> str:
    """Build nsjail protobuf config for agent runtime execution.

    Args:
        rootfs: Path to the sandbox rootfs (same as action sandbox).
        job_dir: Directory for job-specific data.
        socket_dir: Directory containing the per-job control socket
            (control.sock) created by the orchestrator.
        config: Agent sandbox configuration.
        site_packages_dir: Path to site-packages with Claude SDK deps.
        tracecat_pkg_dir: Path to the tracecat package directory.
            Only specific subdirectories are mounted for minimal cold start.
        llm_socket_path: Path to the LLM socket for proxied LiteLLM access.
        enable_internet_access: If True, disables network isolation to allow
            direct internet access. Required for MCP command servers that need
            to call external APIs. Default is False (network isolated).

    Returns:
        nsjail protobuf configuration as a string.

    Raises:
        AgentSandboxValidationError: If any input contains dangerous characters.
    """
    # Validate inputs to prevent injection into protobuf config
    _validate_path(rootfs, "rootfs")
    _validate_path(job_dir, "job_dir")
    _validate_path(socket_dir, "socket_dir")
    _validate_path(site_packages_dir, "site_packages_dir")
    _validate_path(tracecat_pkg_dir, "tracecat_pkg_dir")
    _validate_path(llm_socket_path, "llm_socket_path")

    # Derive control socket path from socket_dir using well-known name
    control_socket_path = socket_dir / CONTROL_SOCKET_NAME
    _validate_path(control_socket_path, "control_socket_path")
    # TRUSTED_MCP_SOCKET_PATH and JAILED_LLM_SOCKET_PATH are constants, no validation needed

    # Network isolation is always enabled (clone_newnet: true)
    # When internet access is needed, we use pasta for userspace networking
    # This provides network isolation while still allowing outbound connections
    lines = [
        'name: "agent_sandbox"',
        "mode: ONCE",
        'hostname: "agent"',
        "keep_env: false",
        "",
        "# Namespace isolation - network namespace is always isolated",
        "clone_newnet: true",
        "clone_newuser: true",
        "clone_newns: true",
        "clone_newpid: true",
        "clone_newipc: true",
        "clone_newuts: true",
        "",
        "# UID/GID mapping - map container user to sandbox user",
        f'uidmap {{ inside_id: "1000" outside_id: "{os.getuid()}" count: 1 }}',
        f'gidmap {{ inside_id: "1000" outside_id: "{os.getgid()}" count: 1 }}',
        "",
        "# Rootfs mounts - read-only base system (same rootfs as action sandbox)",
        f'mount {{ src: "{rootfs}/usr" dst: "/usr" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/lib" dst: "/lib" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/bin" dst: "/bin" is_bind: true rw: false }}',
        f'mount {{ src: "{rootfs}/etc" dst: "/etc" is_bind: true rw: false }}',
    ]

    # Userspace networking via pasta (when internet access is enabled)
    # This provides outbound connectivity while maintaining network namespace isolation
    if enable_internet_access:
        lines.extend(
            [
                "",
                "# Userspace networking via pasta - provides internet access with isolation",
                "user_net {",
                "  enable: true",
                '  ip: "10.255.255.2"',
                '  gw: "10.255.255.1"',
                '  ip6: "fc00::2"',
                '  gw6: "fc00::1"',
                "  enable_dns: true",
                "}",
            ]
        )

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

    # Mount /proc read-only. Combined with clone_newpid: true, the process
    # only sees itself in /proc (PID 1 inside the namespace).
    # Note: subset=pid option would be ideal but fails in Docker due to
    # overmounts on /proc (e.g., /dev/null on /proc/kcore).
    lines.extend(
        [
            "",
            "# /proc - read-only, PID namespace isolated (process only sees itself)",
            'mount { src: "/proc" dst: "/proc" is_bind: true rw: false }',
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
            "# Job directory - contains copied runtime code",
            f'mount {{ src: "{job_dir}" dst: "/work" is_bind: true rw: true }}',
            "",
            "# Site-packages - Claude SDK and other deps (read-only)",
            f'mount {{ src: "{site_packages_dir}" dst: "/site-packages" is_bind: true rw: false }}',
            "",
            "# Tracecat package - minimal subdirectories for fast cold start",
            "# Create directory structure first, then mount specific subdirs",
            'mount { dst: "/app" fstype: "tmpfs" rw: false options: "size=1M" }',
            "",
            "# Parent package __init__.py files for Python import system",
            f'mount {{ src: "{tracecat_pkg_dir}/__init__.py" dst: "/app/tracecat/__init__.py" is_bind: true rw: false }}',
            f'mount {{ src: "{tracecat_pkg_dir}/agent/__init__.py" dst: "/app/tracecat/agent/__init__.py" is_bind: true rw: false }}',
            "",
            "# Mount only what the sandbox entrypoint needs:",
            "# - logger: lightweight loguru wrapper",
            "# - agent/common: lightweight types and protocol",
            "# - agent/runtime: runtime implementations",
            "# - agent/sandbox: entrypoint and llm_bridge",
            "# - agent/mcp: proxy_server and utils",
            f'mount {{ src: "{tracecat_pkg_dir}/logger" dst: "/app/tracecat/logger" is_bind: true rw: false }}',
            f'mount {{ src: "{tracecat_pkg_dir}/agent/common" dst: "/app/tracecat/agent/common" is_bind: true rw: false }}',
            f'mount {{ src: "{tracecat_pkg_dir}/agent/runtime" dst: "/app/tracecat/agent/runtime" is_bind: true rw: false }}',
            f'mount {{ src: "{tracecat_pkg_dir}/agent/sandbox" dst: "/app/tracecat/agent/sandbox" is_bind: true rw: false }}',
            f'mount {{ src: "{tracecat_pkg_dir}/agent/mcp" dst: "/app/tracecat/agent/mcp" is_bind: true rw: false }}',
            "",
            "# Trusted MCP socket (read-only, shared across jobs)",
            f'mount {{ src: "{TRUSTED_MCP_SOCKET_PATH.parent}" dst: "/var/run/tracecat" is_bind: true rw: false }}',
            "",
            "# Per-job control socket",
            f'mount {{ src: "{control_socket_path}" dst: "{JAILED_CONTROL_SOCKET_PATH}" is_bind: true rw: false }}',
            "",
            "# Per-job LLM socket (proxied to LiteLLM on host)",
            f'mount {{ src: "{llm_socket_path}" dst: "{JAILED_LLM_SOCKET_PATH}" is_bind: true rw: false }}',
            "",
            "# Agent home directory with Claude SDK session storage",
            'mount { dst: "/home/agent" fstype: "tmpfs" rw: true options: "size=128M" }',
        ]
    )

    # Network config: pasta provides DNS forwarding at the gateway IP (10.255.255.1)
    # when enable_dns: true. Docker export leaves /etc files empty since Docker
    # manages them at runtime. Write to socket_dir (not job_dir) because job_dir
    # is mounted read-write at /work inside the sandbox.
    if enable_internet_access:
        resolv_conf_path = socket_dir / "resolv.conf"
        resolv_conf_path.write_text("nameserver 10.255.255.1\n")

        hosts_path = socket_dir / "hosts"
        hosts_path.write_text(
            "127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n"
        )

        # nsswitch.conf tells glibc how to resolve hostnames: check /etc/hosts
        # first ("files"), then fall back to DNS. Without this, hostname
        # resolution may fail even with valid /etc/hosts and /etc/resolv.conf.
        nsswitch_path = socket_dir / "nsswitch.conf"
        nsswitch_path.write_text(
            "passwd:         files\n"
            "group:          files\n"
            "shadow:         files\n"
            "hosts:          files dns\n"
            "networks:       files\n"
            "protocols:      files\n"
            "services:       files\n"
        )

        lines.extend(
            [
                "",
                "# Network config - DNS and hostname resolution",
                f'mount {{ src: "{resolv_conf_path}" dst: "/etc/resolv.conf" is_bind: true rw: false }}',
                f'mount {{ src: "{hosts_path}" dst: "/etc/hosts" is_bind: true rw: false }}',
                f'mount {{ src: "{nsswitch_path}" dst: "/etc/nsswitch.conf" is_bind: true rw: false }}',
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

    # Execution settings - run tracecat.agent.sandbox.entrypoint module
    # The entrypoint connects to the control socket at the well-known jailed path
    lines.extend(
        [
            "",
            "# Execution - agent runtime entrypoint module",
            'cwd: "/work"',
            'exec_bin { path: "/usr/local/bin/python3" arg: "-m" arg: "tracecat.agent.sandbox.entrypoint" }',
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
        AgentSandboxValidationError: If any env var key or value is invalid.
    """
    env_map: dict[str, str] = {**AGENT_SANDBOX_BASE_ENV}

    for key, value in config.env_vars.items():
        _validate_env_key(key)
        _validate_env_value(key, value)
        if key in AGENT_SANDBOX_BASE_ENV:
            raise AgentSandboxValidationError(
                f"Cannot override protected env var: {key}"
            )
        env_map[key] = value

    return env_map
