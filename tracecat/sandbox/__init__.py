"""Python sandbox for secure script execution.

This package provides secure sandboxed environments for executing Python scripts.
It supports two execution modes:

1. **nsjail sandbox** (when TRACECAT__DISABLE_NSJAIL=false):
   - Full OS-level isolation via Linux namespaces
   - Network, filesystem, and process isolation
   - Resource limits (memory, CPU, file size)
   - Requires privileged Docker mode or CAP_SYS_ADMIN

2. **Unsafe PID executor** (when TRACECAT__DISABLE_NSJAIL=true, default):
   - Subprocess isolation with venv per dependency set
   - PID namespace isolation when available
   - Works without privileged mode

Both modes support:
- Two-phase execution: package installation → script execution
- Package caching with hash-based keys
- Configurable dependencies and environment variables
"""

from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxValidationError,
)
from tracecat.sandbox.safe_lambda import SafeLambdaValidator, build_safe_lambda
from tracecat.sandbox.service import SandboxService, validate_run_python_script
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, SandboxResult
from tracecat.sandbox.unsafe_pid_executor import (
    UnsafePidExecutor,
)

__all__ = [
    # Services
    "SandboxService",
    "UnsafePidExecutor",
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
    # Validation
    "validate_run_python_script",
    # Security validators
    "SafeLambdaValidator",
    "build_safe_lambda",
]
