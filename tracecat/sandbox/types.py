"""Type definitions for the nsjail Python sandbox."""

from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Any


class SandboxErrorCode(StrEnum):
    """Machine-readable reasons for sandbox execution failures."""

    TIMEOUT = "timeout"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


type IPNetwork = IPv4Network | IPv6Network


class SandboxNetworkProtocol(StrEnum):
    """Network protocol matched by one sandbox egress rule."""

    ANY = "ANY"
    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"


class SandboxNetworkPurpose(StrEnum):
    """Trusted purpose selecting a deployment-owned egress policy."""

    INSTALL = "install"
    REGISTRY = "registry"
    SCRIPT = "script"
    ACTION = "action"
    AGENT = "agent"


class SandboxNetworkMode(StrEnum):
    """Outbound network behavior for an nsjail sandbox."""

    DISABLED = "disabled"
    FILTERED = "filtered"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True, slots=True)
class SandboxEgressRule:
    """One administrator-approved private-network egress exception."""

    destination: IPNetwork
    protocol: SandboxNetworkProtocol = SandboxNetworkProtocol.ANY
    destination_port: int | None = None

    def __post_init__(self) -> None:
        """Validate protocol and port combinations."""
        if not isinstance(self.protocol, SandboxNetworkProtocol):
            raise ValueError("protocol must be a SandboxNetworkProtocol")
        if self.destination_port is None:
            return
        if self.protocol not in {
            SandboxNetworkProtocol.TCP,
            SandboxNetworkProtocol.UDP,
        }:
            raise ValueError("destination ports require TCP or UDP")
        if not 1 <= self.destination_port <= 65535:
            raise ValueError("destination_port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class SandboxNetworkPolicy:
    """Trusted outbound network policy for an nsjail sandbox.

    Attributes:
        mode: Whether networking is disabled, filtered, or unrestricted.
        allowed_rules: Administrator-approved exceptions evaluated before blocks.
        blocked_cidrs: Deployment-specific networks rejected in filtered mode.
        allow_public_ipv6: Allow public IPv6 destinations in filtered mode
            instead of rejecting all IPv6 egress.
    """

    mode: SandboxNetworkMode = SandboxNetworkMode.FILTERED
    allowed_rules: tuple[SandboxEgressRule, ...] = ()
    blocked_cidrs: tuple[IPNetwork, ...] = ()
    allow_public_ipv6: bool = False

    def __post_init__(self) -> None:
        """Reject CIDR rules that cannot affect the selected mode."""
        if not isinstance(self.mode, SandboxNetworkMode):
            raise ValueError("mode must be a SandboxNetworkMode")
        if self.mode is not SandboxNetworkMode.FILTERED and (
            self.allowed_rules or self.blocked_cidrs
        ):
            raise ValueError("egress rules are only valid for filtered networking")
        if self.mode is not SandboxNetworkMode.FILTERED and self.allow_public_ipv6:
            raise ValueError("allow_public_ipv6 is only valid for filtered networking")


@dataclass(frozen=True, slots=True)
class SandboxNetworkRequest:
    """One caller-selected sandbox networking capability.

    Absence of a request means the sandbox has no outbound network backend.
    The purpose selects deployment-owned policy, while ``policy`` is reserved
    for trusted internal overrides such as tests and operator-controlled flows.
    """

    purpose: SandboxNetworkPurpose
    policy: SandboxNetworkPolicy | None = None

    def __post_init__(self) -> None:
        """Keep disabled state and policy selection unambiguous."""
        if not isinstance(self.purpose, SandboxNetworkPurpose):
            raise ValueError("purpose must be a SandboxNetworkPurpose")
        if self.policy is None:
            return
        if not isinstance(self.policy, SandboxNetworkPolicy):
            raise ValueError("policy must be a SandboxNetworkPolicy")
        if self.policy.mode is SandboxNetworkMode.DISABLED:
            raise ValueError("omit the network request to disable networking")


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


@dataclass(frozen=True, slots=True)
class SandboxBindMount:
    """One explicit host-to-sandbox bind mount.

    Attributes:
        source: Existing host path to expose inside the sandbox.
        destination: Absolute path at which the source is visible in the sandbox.
        writable: Whether the sandbox may modify the mounted host path.
    """

    source: Path
    destination: Path
    writable: bool = False


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution.

    Attributes:
        network: Requested outbound capability. None disables networking.
        resources: Resource limits for the sandbox.
        env_vars: Environment variables to inject into the sandbox.
        dependencies: Python packages to install before execution.
        python_path_dirs: Host directories to mount read-only and add to PYTHONPATH.
        bind_mounts: Additional explicit bind mounts for phase-scoped capabilities.
        action_gateway_socket: Optional host-side action gateway Unix socket to
            bind into nsjail for internal Tracecat SDK calls.
    """

    network: SandboxNetworkRequest | None = None
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    env_vars: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    python_path_dirs: list[Path] = field(default_factory=list)
    bind_mounts: list[SandboxBindMount] = field(default_factory=list)
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
