"""Redis client factory with cloud IAM token login.

``config.REDIS_AUTH`` (or an explicit ``auth``) picks how the client logs in:

- ``RedisPasswordAuth`` (default): ``redis.from_url``; any credentials come
  from the URL.
- ``RedisAwsIamAuth``: ElastiCache IAM tokens (``tracecat.redis.aws``).
- ``RedisAzureEntraAuth``: Azure Managed Redis Entra ID tokens
  (``tracecat.redis.azure``).

Each cloud module, and its SDK, is imported only when that mode is used.

Both IAM modes require verified TLS and retry transient handshake errors such
as ElastiCache's ``IAM Authentication service is not available.``

Dropped connections need no extra handling: before handing out an idle pooled
connection, redis-py checks for the server's close and reconnects. A command
in flight when its connection drops still fails, so nothing is replayed.
"""

import asyncio
import ssl
from collections.abc import Mapping
from typing import Any, ClassVar, Final

import redis.asyncio as redis
from redis.asyncio.connection import (
    AbstractConnection,
    ConnectionPool,
    SSLConnection,
    parse_url,
)
from redis.auth.token import TokenInterface
from redis.credentials import CredentialProvider
from redis.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NoPermissionError,
    ResponseError,
)

from tracecat import config
from tracecat.logger import logger
from tracecat.redis.errors import RedisIamAuthUnavailableError
from tracecat.redis.types import (
    AsyncClosable,
    HandshakeRetryPolicy,
    RedisAuth,
    RedisAuthMode,
    RedisAwsIamAuth,
    RedisAzureEntraAuth,
    RedisIamAuth,
    RedisPasswordAuth,
)

IAM_HOST_SUFFIXES: Final[Mapping[RedisAuthMode, tuple[str, ...]]] = {
    RedisAuthMode.AWS_IAM: (".cache.amazonaws.com", ".cache.amazonaws.com.cn"),
    RedisAuthMode.AZURE_ENTRA: (".redis.azure.net", ".redis.cache.windows.net"),
}
"""Hosts each IAM mode sends its token to.

A token minted from the workload identity is valid at the real cache, so it
must not go to whatever host the URL names. The managed endpoints present
certificates for these domains, so verified TLS already rules out others.
"""

_IAM_TLS_OPTIONS: Final = frozenset({"ssl_cert_reqs", "ssl_check_hostname"})
"""TLS options the IAM modes accept. Every other ``ssl_*`` option (CA files or
data, verify flags, ciphers, minimum version, client certificates) could weaken
or re-root verification from the URL. The managed endpoints chain to roots in
the system trust store."""


class IamHandshakeRetry(AbstractConnection):
    """Connection mixin that retries transient errors in the login handshake.

    Rejected credentials and permission denials (``AuthenticationError``,
    ``AuthorizationError``, and ``NoPermissionError`` for a server ``NOPERM``)
    fail at once. Any other server error during the handshake is retried with
    backoff on a fresh socket, per ``handshake_retry``. Any other
    failure closes the socket, so no connection is pooled without a login.
    """

    handshake_retry: ClassVar[HandshakeRetryPolicy] = HandshakeRetryPolicy()

    async def connect_check_health(
        self, check_health: bool = True, retry_socket_connect: bool = True
    ) -> None:
        was_connected = self.is_connected
        policy = self.handshake_retry
        for attempt in range(1, policy.max_attempts + 1):
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
            except (AuthenticationError, AuthorizationError, NoPermissionError):
                raise
            except ResponseError as exc:
                # redis-py already closed the socket after the failed handshake.
                if attempt == policy.max_attempts:
                    raise RedisIamAuthUnavailableError(str(exc)) from exc
                delay = policy.delay(attempt)
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


def build_credential_provider(auth: RedisIamAuth) -> CredentialProvider:
    """Build the credential provider for an IAM login.

    The cloud module is imported here, not at module scope (an exception to
    the backend import rule), so a deployment only loads its own cloud's SDK.
    """
    match auth:
        case RedisAwsIamAuth():
            from tracecat.redis.aws import AwsIamCredentialProvider

            return AwsIamCredentialProvider(auth)
        case RedisAzureEntraAuth():
            from tracecat.redis.azure import AzureEntraCredentialProvider

            return AzureEntraCredentialProvider(auth)


def _validate_iam_url(url: str, options: dict[str, Any], mode: RedisAuthMode) -> None:
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
    if unsupported := sorted(
        key for key in options if key.startswith("ssl_") and key not in _IAM_TLS_OPTIONS
    ):
        raise ValueError(
            f"REDIS_AUTH_MODE={mode} does not accept TLS options {', '.join(unsupported)}"
        )
    host = str(options.get("host", "")).lower().rstrip(".")
    suffixes = IAM_HOST_SUFFIXES[mode]
    if not host.endswith(suffixes):
        raise ValueError(
            f"REDIS_AUTH_MODE={mode} only sends tokens to hosts ending in "
            f"{' or '.join(suffixes)}"
        )


def create_redis_client(
    url: str, *, auth: RedisAuth | None = None, **kwargs: Any
) -> redis.Redis:
    """Create an asyncio Redis client that logs in per ``auth``.

    Takes the same arguments as ``redis.asyncio.Redis.from_url``. ``auth``
    defaults to ``config.REDIS_AUTH``. With ``RedisPasswordAuth`` it is exactly
    ``from_url``. For an IAM login it builds an ``IamConnectionPool`` of
    ``IamSSLConnection`` with the credential provider, after checking the URL
    uses verified TLS and carries no static credentials.

    Close the client with ``close_redis_client`` so a token refresh task stops.
    """
    auth = config.REDIS_AUTH if auth is None else auth
    if isinstance(auth, RedisPasswordAuth):
        return redis.from_url(url, **kwargs)

    # Mirror ConnectionPool.from_url, where URL options win over kwargs.
    # ``ConnectKwargs`` is a TypedDict; widen it to merge with caller kwargs.
    options: dict[str, Any] = {**kwargs, **parse_url(url)}
    _validate_iam_url(url, options, auth.mode)
    provider = build_credential_provider(auth)
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
    if isinstance(provider, AsyncClosable):
        await provider.aclose()
