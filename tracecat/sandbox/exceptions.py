"""Exception classes for the nsjail Python sandbox."""

from tracecat.sandbox.types import SandboxErrorCode


class SandboxError(Exception):
    """Base exception for sandbox errors."""


class SandboxTimeoutError(SandboxError):
    """Execution timed out."""


class SandboxExecutionError(SandboxError):
    """Script execution failed."""


class SandboxInfrastructureError(SandboxError):
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


def raise_for_sandbox_error_code(
    error_code: SandboxErrorCode | None,
    message: str,
) -> None:
    """Raise the typed exception a sandbox result's error code selects."""
    if error_code is SandboxErrorCode.INFRASTRUCTURE_FAILURE:
        raise SandboxInfrastructureError(message)
    if error_code is not None:
        raise SandboxWorkloadError(message, error_code=error_code)
