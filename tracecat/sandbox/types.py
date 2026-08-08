"""Type definitions for the nsjail Python sandbox."""

from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Any


class SandboxErrorCode(StrEnum):
    """Machine-readable reasons for sandbox execution failures."""

    TIMEOUT = "timeout"


type IPNetwork = IPv4Network | IPv6Network


class SandboxNetworkMode(StrEnum):
    """Outbound network behavior for an nsjail sandbox."""

    DISABLED = "disabled"
    FILTERED = "filtered"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True, slots=True)
class SandboxNetworkPolicy:
    """Trusted outbound network policy for an nsjail sandbox.

    Attributes:
        mode: Whether networking is disabled, filtered, or unrestricted.
        allowed_cidrs: Administrator-approved exceptions evaluated before blocks.
        blocked_cidrs: Deployment-specific networks rejected in filtered mode.
    """

    mode: SandboxNetworkMode = SandboxNetworkMode.FILTERED
    allowed_cidrs: tuple[IPNetwork, ...] = ()
    blocked_cidrs: tuple[IPNetwork, ...] = ()

    def __post_init__(self) -> None:
        """Reject CIDR rules that cannot affect the selected mode."""
        if not isinstance(self.mode, SandboxNetworkMode):
            raise ValueError("mode must be a SandboxNetworkMode")
        if self.mode is not SandboxNetworkMode.FILTERED and (
            self.allowed_cidrs or self.blocked_cidrs
        ):
            raise ValueError("CIDR rules are only valid for filtered networking")


@dataclass(frozen=True)
class ResourceLimits:
    """Resource limits for sandbox execution.

    Attributes:
        memory_mb: Maximum memory in megabytes (default 2048 = 2 GiB).
        cpu_seconds: Maximum CPU time in seconds.
        max_file_size_mb: Maximum file size in megabytes.
        max_open_files: Maximum number of open file descriptors.
        max_processes: Maximum number of processes/threads.
        timeout_seconds: Maximum wall-clock execution time.
    """

    memory_mb: int = 2048
    cpu_seconds: int = 300
    max_file_size_mb: int = 256
    max_open_files: int = 256
    max_processes: int = 64
    timeout_seconds: int = 300


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution.

    Attributes:
        network_enabled: Whether to allow network access during script execution.
        network_policy: Trusted egress policy. None uses the deployment policy.
        resources: Resource limits for the sandbox.
        env_vars: Environment variables to inject into the sandbox.
        dependencies: Python packages to install before execution.
        python_path_dirs: Host directories to mount read-only and add to PYTHONPATH.
        action_gateway_socket: Optional host-side action gateway Unix socket to
            bind into nsjail for internal Tracecat SDK calls.
    """

    network_enabled: bool = False
    network_policy: SandboxNetworkPolicy | None = None
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    env_vars: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    python_path_dirs: list[Path] = field(default_factory=list)
    action_gateway_socket: Path | None = None


@dataclass
class SandboxResult:
    """Result of sandbox execution.

    Attributes:
        success: Whether the execution succeeded.
        output: The return value from the executed script.
        stdout: Captured standard output.
        stderr: Captured standard error.
        error: Error message if execution failed.
        error_code: Machine-readable failure reason.
        exit_code: Process exit code.
        execution_time_ms: Execution time in milliseconds.
    """

    success: bool
    output: Any | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    error_code: SandboxErrorCode | None = None
    exit_code: int | None = None
    execution_time_ms: float | None = None
