class AuthPoolExhaustedError(RuntimeError):
    """Raised when the authentication database bulkhead cannot check out a connection."""


class DatabasePoolAcquisitionOrderError(RuntimeError):
    """Raised when database pools are acquired in a deadlock-prone order."""


class AdvisoryLockLostError(RuntimeError):
    """Raised when a commit is attempted after the advisory lock holder ended."""


class DatabaseCredentialUnavailableError(RuntimeError):
    """A cloud token for a Postgres login could not be obtained.

    For example STS or Entra ID is briefly unreachable, or the workload has no
    cloud identity. The connection attempt fails; the next one tries again.
    """
