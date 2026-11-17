"""ElastiCache IAM login for Redis.

Each new connection sends a SigV4-presigned ``connect`` token, signed with the
workload's AWS credentials (IRSA, task role). ElastiCache never asks an open
connection to re-authenticate, so a token only has to be valid when a
connection is opened. boto3 has no ElastiCache helper like RDS's
``generate_db_auth_token``; this presigns the request with botocore's own
signer, as that helper does.

Imported only when ``REDIS_AUTH_MODE=aws_iam``.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode

import boto3
from botocore.model import ServiceId
from botocore.signers import RequestSigner
from redis.credentials import CredentialProvider

from tracecat.redis.errors import RedisCredentialUnavailableError
from tracecat.redis.types import RedisAwsIamAuth


def aws_iam_signing_url(auth: RedisAwsIamAuth) -> str:
    """Return the URL ElastiCache expects a ``connect`` token to be signed for."""
    query = urlencode({"Action": "connect", "User": auth.user_id})
    return f"https://{auth.replication_group_id}/?{query}"


class AwsIamCredentialProvider(CredentialProvider):
    """Signs ElastiCache IAM tokens for new Redis connections.

    A token is signed lazily and reused for ``auth.token.cache_seconds``, well
    inside its ``auth.token.ttl_seconds`` lifetime.
    """

    def __init__(
        self,
        auth: RedisAwsIamAuth,
        *,
        session: boto3.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_id = auth.user_id
        self._policy = auth.token
        self._signing_url = aws_iam_signing_url(auth)
        self._region = auth.region
        self._session = session or boto3.Session(region_name=auth.region)
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._refresh_at = 0.0

    def _cached(self) -> str | None:
        if self._token is not None and self._clock() < self._refresh_at:
            return self._token
        return None

    def _sign(self) -> str:
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials are unavailable for Redis IAM login")
        signer = RequestSigner(
            ServiceId("elasticache"),
            self._region,
            "elasticache",
            "v4",
            credentials,
            self._session.events,
        )
        signed = signer.generate_presigned_url(
            {
                "method": "GET",
                "url": self._signing_url,
                "body": {},
                "headers": {},
                "context": {},
            },
            operation_name="connect",
            expires_in=self._policy.ttl_seconds,
        )
        return signed.removeprefix("https://")

    def get_credentials(self) -> tuple[str, str]:
        with self._lock:
            if (token := self._cached()) is None:
                try:
                    token = self._sign()
                except Exception as exc:
                    raise RedisCredentialUnavailableError(
                        "Could not sign an ElastiCache IAM token"
                    ) from exc
                self._token = token
                self._refresh_at = self._clock() + self._policy.cache_seconds
            return self._user_id, token

    async def get_credentials_async(self) -> tuple[str, str]:
        # Fast path without a thread hop; signing may refresh AWS credentials
        # over the network, so it runs off the event loop.
        if (token := self._cached()) is not None:
            return self._user_id, token
        return await asyncio.to_thread(self.get_credentials)
