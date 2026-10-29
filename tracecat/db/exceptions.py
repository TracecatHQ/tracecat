class AuthPoolExhaustedError(RuntimeError):
    """Raised when the authentication database bulkhead cannot check out a connection."""


class DatabasePoolAcquisitionOrderError(RuntimeError):
    """Raised when database pools are acquired in a deadlock-prone order."""


class AdvisoryLockLostError(RuntimeError):
    """Raised when a commit is attempted after the advisory lock holder ended."""
