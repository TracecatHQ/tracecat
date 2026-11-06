"""Typed Redis login settings.

``tracecat.config.REDIS_AUTH`` holds one of these, parsed from the environment
at import. ``tracecat.redis.auth.create_redis_client`` dispatches on its type.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable

_REPLICATION_GROUP_ID = re.compile(r"[a-z][a-z0-9-]*")


class RedisAuthMode(StrEnum):
    """How the Redis client logs in (``REDIS_AUTH_MODE``)."""

    PASSWORD = "password"
    AWS_IAM = "aws_iam"
    AZURE_ENTRA = "azure_entra"


@dataclass(frozen=True, slots=True)
class HandshakeRetryPolicy:
    """Retries for transient server errors in an IAM login handshake.

    Attributes:
        max_attempts: Handshake attempts per connection before the error is
            raised as a connection error.
        backoff_seconds: Base of the exponential backoff between attempts
            (0.5, 1, 2 s by default).
    """

    max_attempts: int = 4
    backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.backoff_seconds < 0:
            raise ValueError("Handshake retry needs >= 1 attempt and a backoff >= 0")

    def delay(self, attempt: int) -> float:
        """Backoff before retrying after the given (1-based) failed attempt."""
        return self.backoff_seconds * 2 ** (attempt - 1)


@dataclass(frozen=True, slots=True)
class AwsIamTokenPolicy:
    """Lifetime and reuse of signed ElastiCache IAM tokens.

    Attributes:
        ttl_seconds: Lifetime of a signed token. 900 s is the ElastiCache maximum.
        cache_seconds: How long a signed token is reused for new connections
            before re-signing. Must stay below the lifetime.
    """

    ttl_seconds: int = 900
    cache_seconds: int = 600

    def __post_init__(self) -> None:
        if not 0 < self.cache_seconds < self.ttl_seconds <= 900:
            raise ValueError(
                "ElastiCache tokens need 0 < cache_seconds < ttl_seconds <= 900"
            )


@dataclass(frozen=True, slots=True)
class AzureEntraTokenPolicy:
    """Refresh timing for Entra ID tokens.

    Attributes:
        refresh_lead_seconds: Refresh this long before the token expires.
            azure-identity only hands out a new token within 5 minutes of
            expiry, so the lead must stay under 300 s.
        refresh_retry_seconds: Wait between attempts while a refresh fails or
            returns the same token.
        reauth_timeout_seconds: Deadline for moving the pool onto a refreshed
            token, so a stuck login can't stall the refresh task.
    """

    refresh_lead_seconds: float = 240
    refresh_retry_seconds: float = 15
    reauth_timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if not 0 < self.refresh_lead_seconds < 300:
            raise ValueError("refresh_lead_seconds must be between 0 and 300")
        if self.refresh_retry_seconds <= 0 or self.reauth_timeout_seconds <= 0:
            raise ValueError("Entra refresh retry and re-auth timeout must be positive")


@dataclass(frozen=True, slots=True)
class RedisPasswordAuth:
    """Credentials, if any, come from the Redis URL."""

    mode: ClassVar[RedisAuthMode] = RedisAuthMode.PASSWORD


@dataclass(frozen=True, slots=True)
class RedisAwsIamAuth:
    """ElastiCache IAM login, signed with the workload's AWS credentials.

    Attributes:
        user_id: ElastiCache user ID. IAM users must have user ID == user name.
        replication_group_id: Lowercase replication group ID (not the endpoint)
            that tokens are signed for.
        region: Region of the cache.
        token: Token lifetime and reuse.
    """

    user_id: str
    replication_group_id: str
    region: str
    token: AwsIamTokenPolicy = field(default_factory=AwsIamTokenPolicy)

    mode: ClassVar[RedisAuthMode] = RedisAuthMode.AWS_IAM

    def __post_init__(self) -> None:
        if not self.user_id or not self.region:
            raise ValueError("ElastiCache IAM login needs a user ID and a region")
        if not _REPLICATION_GROUP_ID.fullmatch(self.replication_group_id):
            raise ValueError(
                "REDIS_AWS_IAM_REPLICATION_GROUP_ID must be the lowercase replication "
                f"group ID, not an endpoint (got {self.replication_group_id!r})"
            )


@dataclass(frozen=True, slots=True)
class RedisAzureEntraAuth:
    """Azure Managed Redis login with an Entra ID token.

    Attributes:
        client_id: Client ID of the managed or workload identity. ``None`` lets
            DefaultAzureCredential read ``AZURE_CLIENT_ID``.
        token: Refresh timing.
    """

    client_id: str | None = None
    token: AzureEntraTokenPolicy = field(default_factory=AzureEntraTokenPolicy)

    mode: ClassVar[RedisAuthMode] = RedisAuthMode.AZURE_ENTRA


type RedisIamAuth = RedisAwsIamAuth | RedisAzureEntraAuth
type RedisAuth = RedisPasswordAuth | RedisIamAuth


@runtime_checkable
class AsyncClosable(Protocol):
    """A credential provider that holds resources to release (e.g. a refresh task)."""

    async def aclose(self) -> None: ...
