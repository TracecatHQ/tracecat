"""HTTPX transports that bind requests to validated DNS results."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextvars import ContextVar, Token
from typing import cast

import httpcore
import httpx

from tracecat.network import (
    DisallowedUrlError,
    HostResolutionError,
    HttpEgressPolicy,
    HttpOrigin,
    SocketInfo,
    resolve_host_async,
    validate_resolved_addresses,
)

type HostResolver = Callable[[str, int], Awaitable[tuple[SocketInfo, ...]]]


class _RequestOrigin:
    """Task- and thread-local origin for a transport connection attempt."""

    def __init__(self) -> None:
        self._value: ContextVar[HttpOrigin | None] = ContextVar(
            "guarded_http_request_origin", default=None
        )

    def set(self, origin: HttpOrigin) -> Token[HttpOrigin | None]:
        return self._value.set(origin)

    def reset(self, token: Token[HttpOrigin | None]) -> None:
        self._value.reset(token)

    def require(self, host: str, port: int) -> HttpOrigin:
        origin = self._value.get()
        if origin is None or (origin.host, origin.port) != (
            host.rstrip(".").lower(),
            port,
        ):
            raise DisallowedUrlError("Connection target is not allowed")
        return origin


class GuardedAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve, validate, and pin each asynchronous TCP connection."""

    def __init__(
        self,
        policy: HttpEgressPolicy,
        request_origin: _RequestOrigin,
        *,
        resolver: HostResolver = resolve_host_async,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = policy
        self._request_origin = request_origin
        self._resolver = resolver
        self._backend = backend or cast(
            httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend()
        )

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        origin = self._request_origin.require(host, port)
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        try:
            async with asyncio.timeout(timeout):
                try:
                    infos = await self._resolver(host, port)
                    addresses = validate_resolved_addresses(
                        infos,
                        allow_private=self._policy.allows_private_address(origin),
                    )
                except HostResolutionError as exc:
                    # Resolution failures are connection failures and may be
                    # transient. Policy validation below remains terminal.
                    raise httpcore.ConnectError("Host could not be resolved") from exc
                last_error: httpcore.ConnectError | httpcore.ConnectTimeout | None = (
                    None
                )
                for index, address in enumerate(addresses):
                    if deadline is None:
                        attempt_timeout = None
                    else:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise httpcore.ConnectTimeout("Connection timed out")
                        attempt_timeout = remaining / (len(addresses) - index)
                    try:
                        return await self._backend.connect_tcp(
                            str(address),
                            port,
                            timeout=attempt_timeout,
                            local_address=local_address,
                            socket_options=socket_options,
                        )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                        last_error = exc
                raise httpcore.ConnectError("Connection failed") from last_error
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("Connection timed out") from exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise DisallowedUrlError("Unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


def _http_limits(limits: httpx.Limits | None) -> httpx.Limits:
    return limits or httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=5.0,
    )


class GuardedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport whose TCP connections are bound to validated DNS."""

    def __init__(
        self,
        policy: HttpEgressPolicy | None = None,
        *,
        verify: ssl.SSLContext | str | bool = True,
        ca_from_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx.Limits | None = None,
        retries: int = 0,
        resolver: HostResolver = resolve_host_async,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = policy or HttpEgressPolicy()
        self._request_origin = _RequestOrigin()
        effective_limits = _http_limits(limits)
        ssl_context = httpx.create_ssl_context(verify=verify, trust_env=ca_from_env)
        network_backend = GuardedAsyncNetworkBackend(
            self._policy,
            self._request_origin,
            resolver=resolver,
            backend=backend,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=effective_limits.max_connections,
            max_keepalive_connections=effective_limits.max_keepalive_connections,
            keepalive_expiry=effective_limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            retries=retries,
            network_backend=network_backend,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        origin = HttpOrigin.from_url(request.url)
        token = self._request_origin.set(origin)
        try:
            return await super().handle_async_request(request)
        finally:
            self._request_origin.reset(token)


def guarded_async_client(
    policy: HttpEgressPolicy | None = None,
    *,
    auth: httpx.Auth | tuple[str, str] | None = None,
    base_url: str | httpx.URL = "",
    headers: httpx.Headers | Mapping[str, str] | None = None,
    timeout: float | httpx.Timeout | None = 5.0,
    follow_redirects: bool = False,
    max_redirects: int = 20,
    verify: ssl.SSLContext | str | bool = True,
    ca_from_env: bool = True,
    http1: bool = True,
    http2: bool = False,
    limits: httpx.Limits | None = None,
    retries: int = 0,
    resolver: HostResolver = resolve_host_async,
    backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncClient:
    """Create a guarded client with all proxy routing disabled.

    HTTPX can replace a custom transport when a caller also supplies ``proxy``
    or ``mounts``. Keeping client construction here makes those bypass options
    unavailable, while still allowing deployment CA settings to be inherited.
    """
    transport = GuardedAsyncHTTPTransport(
        policy,
        verify=verify,
        ca_from_env=ca_from_env,
        http1=http1,
        http2=http2,
        limits=limits,
        retries=retries,
        resolver=resolver,
        backend=backend,
    )
    return httpx.AsyncClient(
        auth=auth,
        base_url=base_url,
        headers=headers,
        timeout=timeout,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        transport=transport,
        trust_env=False,
    )
