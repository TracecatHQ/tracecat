"""Cloud IAM token login for Redis.

``REDIS_AUTH_MODE`` picks how the Redis client logs in:

- ``password`` (default): ``redis.from_url``; any credentials come from the
  URL.
- ``aws_iam``: ElastiCache IAM. Each new connection sends a SigV4-presigned
  ``connect`` token, signed with the workload's AWS credentials (IRSA, task
  role). ElastiCache never asks an open connection to re-authenticate, so a
  token only has to be valid when a connection is opened.
- ``azure_entra``: Azure Managed Redis with an Entra ID token. Azure closes a
  connection whose token expires, so the provider refreshes the token before
  expiry and the pool moves every connection onto the new token.

Both IAM modes require verified TLS and retry transient handshake errors such
as ElastiCache's ``IAM Authentication service is not available.``

Dropped connections need no extra handling: before handing out an idle pooled
connection, redis-py checks for the server's close and reconnects. A command
in flight when its connection drops still fails, so nothing is replayed.
"""

import asyncio
import base64
import re
import ssl
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import boto3
import orjson
import redis.asyncio as redis
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential
from botocore.model import ServiceId
from botocore.signers import RequestSigner
from redis.asyncio.connection import (
    AbstractConnection,
    ConnectionPool,
    SSLConnection,
    parse_url,
)
from redis.auth.token import SimpleToken, TokenInterface
from redis.credentials import CredentialProvider, StreamingCredentialProvider
from redis.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError,
    ResponseError,
)

from tracecat import config
from tracecat.logger import logger

AWS_IAM_TOKEN_TTL_SECONDS = 900
"""Lifetime of a signed ElastiCache token. 15 minutes is the ElastiCache maximum."""

AWS_IAM_TOKEN_CACHE_SECONDS = 600
"""How long a signed token is reused for new connections before re-signing."""

AZURE_REFRESH_LEAD_SECONDS = 240
"""Refresh the Entra token this long before it expires.

azure-identity only hands out a new token within 5 minutes of expiry, so the
lead must stay under 300 seconds for a refresh to return a different token.
"""

AZURE_REFRESH_RETRY_SECONDS = 15
"""Wait between attempts while a refresh fails or returns the same token."""

AZURE_REAUTH_TIMEOUT_SECONDS = 30
"""Deadline for moving the pool onto a refreshed token.

The pool lock is also held while a new connection logs in; the deadline keeps
a stuck login from stalling the refresh task.
"""

AZURE_REDIS_SCOPE = "https://redis.azure.com/.default"
"""Entra token scope (audience) for Azure Managed Redis."""

IAM_AUTH_MAX_ATTEMPTS = 4
"""Handshake attempts per connection before a transient auth error is raised."""

IAM_AUTH_BACKOFF_SECONDS = 0.5
"""Base for the exponential backoff between handshake attempts (0.5, 1, 2 s)."""

_REPLICATION_GROUP_ID = re.compile(r"[a-z][a-z0-9-]*")

type ReAuthCallback = Callable[[TokenInterface], Awaitable[None] | None]


class RedisIamAuthUnavailableError(ConnectionError):
    """The Redis handshake kept failing with a non-credential server error.

    ElastiCache answers ``AUTH`` with ``IAM Authentication service is not
    available.`` while IAM is briefly unavailable or a policy change is
    propagating. That is not a rejected credential, so it is raised as a
    ``ConnectionError`` and callers treat it like any other dropped connection.
    """


class RedisCredentialUnavailableError(ConnectionError):
    """The cloud credential for a Redis login could not be obtained.

    For example STS or Entra ID is briefly unreachable. Raised as a
    ``ConnectionError`` so callers retry it like a dropped connection, and so
    redis-py closes the half-opened socket instead of pooling it unauthenticated.
    """


class IamHandshakeRetry(AbstractConnection):
    """Connection mixin that retries transient errors in the login handshake.

    Rejected credentials (``AuthenticationError``, ``AuthorizationError``) fail
    at once. Any other server error during the handshake is retried with
    backoff on a fresh socket, up to ``IAM_AUTH_MAX_ATTEMPTS``. Any other
    failure closes the socket, so no connection is pooled without a login.
    """

    async def connect_check_health(
        self, check_health: bool = True, retry_socket_connect: bool = True
    ) -> None:
        was_connected = self.is_connected
        for attempt in range(1, IAM_AUTH_MAX_ATTEMPTS + 1):
            try:
                await super().connect_check_health(
                    check_health=check_health,
                    retry_socket_connect=retry_socket_connect,
                )
                if not was_connected:
                    # A fresh login has the current token; clear any recycle
                    # mark from IamConnectionPool (redis-py never clears it).
                    self._should_reconnect = False
                return
            except (AuthenticationError, AuthorizationError):
                raise
            except ResponseError as exc:
                # redis-py already closed the socket after the failed handshake.
                if attempt == IAM_AUTH_MAX_ATTEMPTS:
                    raise RedisIamAuthUnavailableError(str(exc)) from exc
                delay = IAM_AUTH_BACKOFF_SECONDS * 2 ** (attempt - 1)
                logger.warning(
                    "Redis IAM login failed with a transient server error, retrying",
                    error=str(exc),
                    attempt=attempt,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
            except BaseException:
                await self.disconnect(nowait=True)
                raise


class IamSSLConnection(IamHandshakeRetry, SSLConnection):
    """Verified-TLS connection used by the IAM auth modes."""


class IamConnectionPool(ConnectionPool):
    """Connection pool that moves its connections onto a refreshed token.

    redis-py 7.1 re-AUTHs a busy connection only after it is back in the pool:
    outside the pool lock, so another task can check it out mid-AUTH, and with
    no deadline, so a half-open socket blocks every later release. Instead,
    idle connections are closed at once and busy ones when they are released.
    Each logs in again with the current token on its next checkout.
    """

    async def re_auth_callback(self, token: TokenInterface) -> None:
        del token  # New logins read the current token from the provider.
        async with self._lock:
            for connection in self._available_connections:
                await connection.disconnect(nowait=True)
            for connection in self._in_use_connections:
                connection.mark_for_reconnect()


def aws_iam_signing_url(replication_group_id: str, user_id: str) -> str:
    """Return the URL ElastiCache expects a ``connect`` token to be signed for."""
    if not _REPLICATION_GROUP_ID.fullmatch(replication_group_id):
        raise ValueError(
            "REDIS_AWS_IAM_REPLICATION_GROUP_ID must be the lowercase replication "
            f"group ID, not an endpoint (got {replication_group_id!r})"
        )
    query = urlencode({"Action": "connect", "User": user_id})
    return f"https://{replication_group_id}/?{query}"


class AwsIamCredentialProvider(CredentialProvider):
    """Signs ElastiCache IAM tokens for new Redis connections.

    A token is signed lazily and reused for ``AWS_IAM_TOKEN_CACHE_SECONDS``,
    well inside its ``AWS_IAM_TOKEN_TTL_SECONDS`` lifetime.
    """

    def __init__(
        self,
        *,
        user_id: str,
        replication_group_id: str,
        region: str,
        session: boto3.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_id = user_id
        self._signing_url = aws_iam_signing_url(replication_group_id, user_id)
        self._region = region
        self._session = session or boto3.Session(region_name=region)
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
            expires_in=AWS_IAM_TOKEN_TTL_SECONDS,
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
                self._refresh_at = self._clock() + AWS_IAM_TOKEN_CACHE_SECONDS
            return self._user_id, token

    async def get_credentials_async(self) -> tuple[str, str]:
        # Fast path without a thread hop; signing may refresh AWS credentials
        # over the network, so it runs off the event loop.
        if (token := self._cached()) is not None:
            return self._user_id, token
        return await asyncio.to_thread(self.get_credentials)


def _token_username(token: str) -> str:
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
    refreshes it ``AZURE_REFRESH_LEAD_SECONDS`` before expiry and hands the new
    token to redis-py, which calls ``IamConnectionPool.re_auth_callback`` to
    move the pool's connections onto it.

    One provider serves one connection pool: redis-py registers a single
    re-auth callback per provider. Only the asyncio client is supported.
    """

    def __init__(
        self,
        *,
        scope: str = AZURE_REDIS_SCOPE,
        client_id: str | None = None,
        credential_factory: Callable[[], AsyncTokenCredential] | None = None,
    ) -> None:
        self._scope = scope
        self._client_id = client_id
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
        return await self._credential.get_token(self._scope)

    async def _current_token(self) -> AccessToken:
        async with self._lock:
            token = self._token
            if (
                token is None
                or token.expires_on - time.time() <= AZURE_REFRESH_LEAD_SECONDS
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
                current.expires_on - time.time() - AZURE_REFRESH_LEAD_SECONDS,
                AZURE_REFRESH_RETRY_SECONDS,
            )
            await asyncio.sleep(delay)
            try:
                async with self._lock:
                    token = await self._fetch_token()
                    changed = token.token != current.token
                    self._token = token
            except Exception as exc:
                logger.warning(
                    "Failed to refresh the Redis Entra token, retrying",
                    error_type=type(exc).__name__,
                )
                continue
            if changed:
                await self._publish(token)

    async def _publish(self, token: AccessToken) -> None:
        if self._on_next is None:
            return
        now_ms = time.time() * 1000
        redis_token = SimpleToken(
            token.token,
            token.expires_on * 1000,
            now_ms,
            {"oid": _token_username(token.token)},
        )
        try:
            if (result := self._on_next(redis_token)) is not None:
                await asyncio.wait_for(result, timeout=AZURE_REAUTH_TIMEOUT_SECONDS)
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
            username = _token_username(token.token)
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


def build_credential_provider() -> (
    AwsIamCredentialProvider | AzureEntraCredentialProvider
):
    """Build the credential provider for the configured IAM auth mode."""
    match config.REDIS_AUTH_MODE:
        case "aws_iam":
            return AwsIamCredentialProvider(
                user_id=config.REDIS_AWS_IAM_USER_ID,
                replication_group_id=config.REDIS_AWS_IAM_REPLICATION_GROUP_ID,
                region=config.REDIS_AWS_REGION,
            )
        case "azure_entra":
            return AzureEntraCredentialProvider(client_id=config.REDIS_AZURE_CLIENT_ID)
        case "password":
            raise ValueError("Password mode does not use a credential provider")


def _validate_iam_url(url: str, options: dict[str, Any]) -> None:
    mode = config.REDIS_AUTH_MODE
    if not url.startswith("rediss://"):
        raise ValueError(
            f"REDIS_AUTH_MODE={mode} requires TLS: the Redis URL must use rediss://"
        )
    if options.get("username") or options.get("password"):
        raise ValueError(
            f"REDIS_AUTH_MODE={mode} logs in with a token: remove the username "
            "and password from the Redis URL"
        )
    if options.get("ssl_cert_reqs", "required") not in ("required", ssl.CERT_REQUIRED):
        raise ValueError(f"REDIS_AUTH_MODE={mode} requires ssl_cert_reqs=required")
    if not options.get("ssl_check_hostname", True):
        raise ValueError(f"REDIS_AUTH_MODE={mode} requires ssl_check_hostname")


def create_redis_client(url: str, **kwargs: Any) -> redis.Redis:
    """Create an asyncio Redis client that logs in per ``REDIS_AUTH_MODE``.

    Takes the same arguments as ``redis.asyncio.Redis.from_url``. In password
    mode it is exactly ``from_url``. In the IAM modes it builds an
    ``IamConnectionPool`` of ``IamSSLConnection`` with the credential provider,
    after checking the URL uses verified TLS and carries no static credentials.

    Close the client with ``close_redis_client`` so a token refresh task stops.
    """
    if config.REDIS_AUTH_MODE == "password":
        return redis.from_url(url, **kwargs)

    # Mirror ConnectionPool.from_url, where URL options win over kwargs.
    # ``ConnectKwargs`` is a TypedDict; widen it to merge with caller kwargs.
    options: dict[str, Any] = {**kwargs, **parse_url(url)}
    _validate_iam_url(url, options)
    provider = build_credential_provider()
    options["connection_class"] = IamSSLConnection
    options["credential_provider"] = provider
    pool = IamConnectionPool(**options)
    client = redis.Redis(connection_pool=pool, credential_provider=provider)
    # Like from_url: closing the client also closes the pool it created.
    client.auto_close_connection_pool = True
    return client


async def close_redis_client(client: redis.Redis) -> None:
    """Close a client from ``create_redis_client`` and stop its token refresh."""
    pool = client.connection_pool
    await client.aclose()
    await pool.disconnect()
    provider = pool.connection_kwargs.get("credential_provider")
    if isinstance(provider, AzureEntraCredentialProvider):
        await provider.aclose()
