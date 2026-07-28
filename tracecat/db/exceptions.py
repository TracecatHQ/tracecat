class AuthPoolExhaustedError(RuntimeError):
    """Raised when the authentication database bulkhead cannot check out a connection."""


class DatabasePoolAcquisitionOrderError(RuntimeError):
    """Raised when database pools are acquired in a deadlock-prone order."""
