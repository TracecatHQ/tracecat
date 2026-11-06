"""Azure Managed Redis login with Entra ID tokens.

Tokens come from azure-identity's ``DefaultAzureCredential``. Azure closes a
connection whose token expires, so the provider refreshes the token before
expiry and hands it to redis-py, which calls the pool's re-auth hook
(``tracecat.redis.auth.IamConnectionPool``) to move connections onto it.

Imported only when ``REDIS_AUTH_MODE=azure_entra``.
"""

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

import orjson
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from redis.auth.token import SimpleToken, TokenInterface
from redis.credentials import StreamingCredentialProvider

from tracecat.logger import logger
from tracecat.redis.errors import RedisCredentialUnavailableError
from tracecat.redis.types import RedisAzureEntraAuth

AZURE_REDIS_SCOPE: Final = "https://redis.azure.com/.default"
"""Entra token scope (audience) for Azure Managed Redis."""

type ReAuthCallback = Callable[[TokenInterface], Awaitable[None] | None]


def token_username(token: str) -> str:
    """Return the token's object ID, the user name Azure Managed Redis expects.

    The JWT is decoded without verification only to read this claim; Redis
    validates the token itself.
    """
    try:
        payload = token.split(".")[1]
        claims = orjson.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except (IndexError, ValueError) as exc:
        raise ValueError("Entra token is not a JWT") from exc
    match claims:
        case {"oid": str(oid)} if oid:
            return oid
        case {"sub": str(sub)} if sub:
            return sub
    raise ValueError("Entra token has no oid or sub claim")


class AzureEntraCredentialProvider(StreamingCredentialProvider):
    """Entra ID tokens for Azure Managed Redis, re-sent before they expire.

    New connections log in with the current token. A background task
    refreshes it ``auth.token.refresh_lead_seconds`` before expiry and hands
    the new token to redis-py, which calls the pool's re-auth hook to move the
    pool's connections onto it.

    One provider serves one connection pool: redis-py registers a single
    re-auth callback per provider. Only the asyncio client is supported.
    """

    def __init__(
        self,
        auth: RedisAzureEntraAuth,
        *,
        credential_factory: Callable[[], AsyncTokenCredential] | None = None,
    ) -> None:
        self._client_id = auth.client_id
        self._policy = auth.token
        self._credential_factory = credential_factory or self._default_credential
        self._credential: AsyncTokenCredential | None = None
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._on_next: ReAuthCallback | None = None

    def _default_credential(self) -> AsyncTokenCredential:
        if self._client_id is None:
            return DefaultAzureCredential()
        return DefaultAzureCredential(
            managed_identity_client_id=self._client_id,
            workload_identity_client_id=self._client_id,
        )

    async def _fetch_token(self) -> AccessToken:
        if self._credential is None:
            self._credential = self._credential_factory()
        return await self._credential.get_token(AZURE_REDIS_SCOPE)

    async def _current_token(self) -> AccessToken:
        async with self._lock:
            token = self._token
            if (
                token is None
                or token.expires_on - time.time() <= self._policy.refresh_lead_seconds
            ):
                token = await self._fetch_token()
                self._token = token
            return token

    def _ensure_refresh_task(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(
                self._refresh_loop(), name="redis-entra-token-refresh"
            )

    async def _refresh_loop(self) -> None:
        while True:
            current = self._token
            if current is None:
                return
            delay = max(
                current.expires_on - time.time() - self._policy.refresh_lead_seconds,
                self._policy.refresh_retry_seconds,
            )
            await asyncio.sleep(delay)
            try:
                async with self._lock:
                    token = await self._fetch_token()
                    # Keep the previous token if this one is unusable.
                    username = token_username(token.token)
                    changed = token.token != current.token
                    self._token = token
            except Exception as exc:
                logger.warning(
                    "Failed to refresh the Redis Entra token, retrying",
                    error_type=type(exc).__name__,
                )
                continue
            if changed:
                await self._publish(token, username)

    async def _publish(self, token: AccessToken, username: str) -> None:
        if self._on_next is None:
            return
        now_ms = time.time() * 1000
        redis_token = SimpleToken(
            token.token, token.expires_on * 1000, now_ms, {"oid": username}
        )
        try:
            if (result := self._on_next(redis_token)) is not None:
                await asyncio.wait_for(
                    result, timeout=self._policy.reauth_timeout_seconds
                )
        except Exception as exc:
            # Connections that miss the re-AUTH are closed by Azure at expiry
            # and reconnect with the new token.
            logger.warning(
                "Failed to re-authenticate pooled Redis connections",
                error_type=type(exc).__name__,
            )

    def get_credentials(self) -> tuple[str, str]:
        raise NotImplementedError(
            "Azure Entra Redis login supports the asyncio client only"
        )

    async def get_credentials_async(self) -> tuple[str, str]:
        try:
            token = await self._current_token()
            username = token_username(token.token)
        except Exception as exc:
            raise RedisCredentialUnavailableError(
                "Could not get an Entra ID token for Redis"
            ) from exc
        self._ensure_refresh_task()
        return username, token.token

    def on_next(self, callback: ReAuthCallback) -> None:
        self._on_next = callback

    def on_error(self, callback: Callable[[Exception], Any]) -> None:
        # Refresh failures are logged and retried in _refresh_loop. redis-py's
        # handler only re-raises, which would end the refresh task.
        del callback

    def is_streaming(self) -> bool:
        return True

    async def aclose(self) -> None:
        """Stop refreshing and close the Azure credential.

        A later login (for example from a client still holding the old pool)
        starts a fresh credential and refresh task.
        """
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self._credential is not None:
            credential, self._credential = self._credential, None
            await credential.close()
