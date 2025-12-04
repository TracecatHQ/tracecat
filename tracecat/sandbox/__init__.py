"""nsjail-based Python sandbox for secure script execution.

This package provides a secure sandboxed environment for executing Python scripts
using nsjail for isolation. It supports:
- Two-phase execution: package installation (with network) → script execution
- Package caching with hash-based keys
- Configurable network access and resource limits
- Secure secrets injection via environment variables
"""

from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxValidationError,
)
from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, SandboxResult

__all__ = [
    # Service
    "SandboxService",
    # Types
    "ResourceLimits",
    "SandboxConfig",
    "SandboxResult",
    # Exceptions
    "SandboxError",
    "SandboxTimeoutError",
    "SandboxExecutionError",
    "SandboxValidationError",
    "PackageInstallError",
]
