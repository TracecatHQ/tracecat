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


def sandbox_resource_limit_message(*, memory_mb: int, memory_env_var: str) -> str:
    """Return the user-facing explanation for a sandbox resource-limit failure.

    The message names every limit the sandbox enforces and the memory cap the
    deployment configured, because the address-space cap is the limit a
    workload most often hits. It deliberately carries no host paths or
    sandbox-controlled text.
    """
    return (
        "The sandbox exceeded a resource limit (memory, CPU time, file size, "
        f"or process count). Memory is capped at {memory_mb} MB of address "
        f"space by {memory_env_var}."
    )


def raise_for_sandbox_error_code(
    error_code: SandboxErrorCode | None,
    message: str,
) -> None:
    """Raise the typed exception a sandbox result's error code selects."""
    if error_code is SandboxErrorCode.INFRASTRUCTURE_FAILURE:
        raise SandboxInfrastructureError(message)
    if error_code is not None:
        raise SandboxWorkloadError(message, error_code=error_code)


class SandboxFileSafetyError(SandboxError):
    """A sandbox-controlled file failed a host-side safety check."""
