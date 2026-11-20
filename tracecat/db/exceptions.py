import uuid


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


class TenantBusyError(RuntimeError):
    """An organization is at its per-process DB session cap.

    Raised before the operation takes a connection or has any effect, so the
    caller can retry it safely after ``retry_after_seconds``.
    """

    def __init__(
        self,
        message: str,
        *,
        organization_id: uuid.UUID,
        cap: int,
        retry_after_seconds: int,
    ) -> None:
        super().__init__(message)
        self.organization_id = organization_id
        self.cap = cap
        self.retry_after_seconds = retry_after_seconds
