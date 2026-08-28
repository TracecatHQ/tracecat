"""Exception classes for the nsjail Python sandbox."""

from tracecat.sandbox.types import SandboxErrorCode


class SandboxError(Exception):
    """Base exception for sandbox errors."""


class SandboxTimeoutError(SandboxError):
    """Execution timed out."""


class SandboxExecutionError(SandboxError):
    """Script execution failed."""


class SandboxInfrastructureError(SandboxExecutionError):
    """Sandbox host or setup failed independently of the workload."""


class SandboxWorkloadError(SandboxError):
    """Sandboxed workload stopped without producing a structured result."""

    def __init__(self, message: str, *, error_code: SandboxErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


class PackageInstallError(SandboxError):
    """Package installation failed."""


class SandboxValidationError(SandboxError):
    """Input validation failed for sandbox configuration."""
