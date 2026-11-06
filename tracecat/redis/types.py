"""Typed Redis login settings.

``tracecat.config.REDIS_AUTH`` holds one of these, parsed from the environment
at import. ``tracecat.redis.auth.create_redis_client`` dispatches on its type.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

_REPLICATION_GROUP_ID = re.compile(r"[a-z][a-z0-9-]*")


class RedisAuthMode(StrEnum):
    """How the Redis client logs in (``REDIS_AUTH_MODE``)."""

    PASSWORD = "password"
    AWS_IAM = "aws_iam"
    AZURE_ENTRA = "azure_entra"


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
    """

    user_id: str
    replication_group_id: str
    region: str

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
    """

    client_id: str | None = None

    mode: ClassVar[RedisAuthMode] = RedisAuthMode.AZURE_ENTRA


type RedisIamAuth = RedisAwsIamAuth | RedisAzureEntraAuth
type RedisAuth = RedisPasswordAuth | RedisIamAuth
