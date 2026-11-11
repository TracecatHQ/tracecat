"""RDS IAM login for Postgres.

Each new connection sends a SigV4-presigned ``connect`` token as its password,
signed locally with the workload's AWS credentials (IRSA, Pod Identity, ECS
task role). Postgres checks the token only at login, so an open connection
outlives it.

Imported only when ``TRACECAT__DB_AUTH_MODE=aws_iam``.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Protocol

import boto3

from tracecat.db.exceptions import DatabaseCredentialUnavailableError
from tracecat.db.iam.types import DbAwsIamAuth


class RdsClient(Protocol):
    """The part of boto3's RDS client used here (types-boto3 has no RDS stubs)."""

    def generate_db_auth_token(
        self, DBHostname: str, Port: int, DBUsername: str, Region: str | None = None
    ) -> str: ...

    def close(self) -> None: ...


class RdsIamTokenProvider:
    """Signs RDS IAM tokens for new Postgres connections.

    A token is signed lazily and reused for ``auth.cache_seconds``, well inside
    its 900 s lifetime. Reuse keeps a pooler that validates each new token
    against RDS (PgDog's passthrough) from logging in once per connection.
    """

    def __init__(
        self,
        auth: DbAwsIamAuth,
        username: str,
        *,
        session: boto3.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._auth = auth
        self._username = username
        self._session = session
        # Created on first use, off the event loop: creating it can resolve
        # credentials over the network (container or Pod Identity endpoint).
        self._client: RdsClient | None = None
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._refresh_at = 0.0

    def _cached(self) -> str | None:
        if self._token is not None and self._clock() < self._refresh_at:
            return self._token
        return None

    def mint(self) -> str:
        """Return a valid token, signing a new one when the cached one is due.

        Raises:
            DatabaseCredentialUnavailableError: No AWS credentials, or they
                could not be refreshed.
        """
        with self._lock:
            if (token := self._cached()) is not None:
                return token
            try:
                if (client := self._client) is None:
                    session = self._session or boto3.Session(
                        region_name=self._auth.region
                    )
                    client = self._client = session.client(
                        "rds", region_name=self._auth.region
                    )
                token = client.generate_db_auth_token(
                    DBHostname=self._auth.host,
                    Port=self._auth.port,
                    DBUsername=self._username,
                    Region=self._auth.region,
                )
            except Exception as exc:
                raise DatabaseCredentialUnavailableError(
                    "Could not sign an RDS IAM token"
                ) from exc
            self._token = token
            self._refresh_at = self._clock() + self._auth.cache_seconds
            return token

    async def __call__(self) -> str:
        # Fast path without a thread hop; signing may refresh AWS credentials
        # over the network (STS), so it runs off the event loop.
        if (token := self._cached()) is not None:
            return token
        return await asyncio.to_thread(self.mint)

    async def aclose(self) -> None:
        if (client := self._client) is not None:
            self._client = None
            client.close()
