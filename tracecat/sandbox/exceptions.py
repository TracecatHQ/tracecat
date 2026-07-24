"""Exception classes for the nsjail Python sandbox."""


class SandboxError(Exception):
    """Base exception for sandbox errors."""


class SandboxTimeoutError(SandboxError):
    """Execution timed out."""


class SandboxExecutionError(SandboxError):
    """Script execution failed."""


class PackageInstallError(SandboxError):
    """Package installation failed."""


class SandboxValidationError(SandboxError):
    """Input validation failed for sandbox configuration."""


class SandboxFileSafetyError(SandboxError):
    """A sandbox-controlled file failed a host-side safety check."""
