"""Typed Postgres login settings.

``tracecat.config.TRACECAT__DB_AUTH`` holds one of these, parsed from the
environment at import. ``tracecat.db.iam.tokens.build_token_provider`` turns a
token mode into the async password callable asyncpg calls for each new
connection.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Protocol

RDS_IAM_TOKEN_TTL_SECONDS: Final = 900
"""Lifetime of an RDS IAM token (``generate_db_auth_token`` signs for 15 min)."""

TOKEN_SSLMODES: Final = frozenset({"require", "verify-ca", "verify-full"})
"""``TRACECAT__DB_SSLMODE`` values the token modes accept: a token must never
travel in plaintext, and ``prefer`` falls back to plaintext silently."""


class DbAuthMode(StrEnum):
    """How Tracecat logs in to Postgres (``TRACECAT__DB_AUTH_MODE``)."""

    PASSWORD = "password"
    AWS_IAM = "aws_iam"
    AZURE_ENTRA = "azure_entra"


@dataclass(frozen=True, slots=True)
class DbPasswordAuth:
    """A static password from ``TRACECAT__DB_PASS``, Secrets Manager or the URI."""

    mode: ClassVar[DbAuthMode] = DbAuthMode.PASSWORD


@dataclass(frozen=True, slots=True)
class DbAwsIamAuth:
    """RDS IAM login, signed with the workload's AWS credentials.

    The token is signed for the RDS endpoint, which differs from the host the
    app connects to when a pooler such as PgDog passes the token through.

    Attributes:
        host: RDS endpoint the token is signed for.
        port: Port of that endpoint.
        region: Region of the database.
        cache_seconds: How long a signed token is reused for new connections
            before re-signing. Must stay below the 900 s token lifetime.
    """

    host: str
    port: int
    region: str
    cache_seconds: int = 600

    mode: ClassVar[DbAuthMode] = DbAuthMode.AWS_IAM

    def __post_init__(self) -> None:
        if not self.host or not self.region:
            raise ValueError("RDS IAM login needs the RDS endpoint and a region")
        if not 0 < self.port < 65536:
            raise ValueError(f"RDS IAM login needs a valid port (got {self.port})")
        if not 0 < self.cache_seconds < RDS_IAM_TOKEN_TTL_SECONDS:
            raise ValueError(
                f"RDS IAM tokens need 0 < cache_seconds < {RDS_IAM_TOKEN_TTL_SECONDS}"
            )


@dataclass(frozen=True, slots=True)
class DbAzureEntraAuth:
    """Azure Database for PostgreSQL login with an Entra ID token.

    Attributes:
        client_id: Client ID of the managed or workload identity. ``None`` lets
            DefaultAzureCredential read ``AZURE_CLIENT_ID``.
    """

    client_id: str | None = None

    mode: ClassVar[DbAuthMode] = DbAuthMode.AZURE_ENTRA


type DbTokenAuth = DbAwsIamAuth | DbAzureEntraAuth
type DbAuth = DbPasswordAuth | DbTokenAuth


class DbTokenProvider(Protocol):
    """Async password callable for asyncpg: returns a valid token on each call."""

    async def __call__(self) -> str: ...

    async def aclose(self) -> None:
        """Release the cloud client or credential."""
        ...
