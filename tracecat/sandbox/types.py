"""Type definitions for the nsjail Python sandbox."""

from dataclasses import dataclass, field
from typing import Any


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
        resources: Resource limits for the sandbox.
        env_vars: Environment variables to inject into the sandbox.
        dependencies: Python packages to install before execution.
    """

    network_enabled: bool = False
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    env_vars: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class SandboxResult:
    """Result of sandbox execution.

    Attributes:
        success: Whether the execution succeeded.
        output: The return value from the executed script.
        stdout: Captured standard output.
        stderr: Captured standard error.
        error: Error message if execution failed.
        exit_code: Process exit code.
        execution_time_ms: Execution time in milliseconds.
    """

    success: bool
    output: Any | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    exit_code: int | None = None
    execution_time_ms: float | None = None
