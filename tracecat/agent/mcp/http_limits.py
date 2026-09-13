"""Byte-capped httpx transport for user MCP HTTP/SSE clients.

A malicious user MCP server can OOM the in-process trusted server: the mcp SDK
calls ``response.aread()`` with no size limit and httpx auto-decompresses gzip.
This module forces identity encoding and enforces a hard per-response byte cap
at the transport layer, below httpx's decode step.

The cap is per-response and is only safe because ``UserMCPClient`` opens a fresh
client per tool call (short-lived sessions). A long-lived client with a
persistent SSE stream would accumulate bytes across messages and break this
assumption.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from tracecat.network import (
    DisallowedUrlError,
    HttpEgressPurpose,
    HttpOrigin,
    configured_http_egress_policy,
)
from tracecat.outbound_http import GuardedAsyncHTTPTransport

MCP_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class MCPResponseTooLargeError(Exception):
    """Raised when a user MCP response exceeds the byte cap."""

    def __init__(self, limit: int, observed: int | None = None) -> None:
        self.limit = limit
        self.observed = observed
        detail = (
            f" (observed at least {observed} bytes)" if observed is not None else ""
        )
        super().__init__(f"MCP server response exceeded {limit} byte limit{detail}")


class _CountingByteStream(httpx.AsyncByteStream):
    """Wrap a byte stream and abort once cumulative bytes exceed the cap."""

    def __init__(self, stream: httpx.AsyncByteStream, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._seen = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            self._seen += len(chunk)
            if self._seen > self._limit:
                raise MCPResponseTooLargeError(self._limit, observed=self._seen)
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class BoundedResponseTransport(httpx.AsyncBaseTransport):
    """Force identity encoding and cap response bytes on the wrapped transport."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        limit: int = MCP_MAX_RESPONSE_BYTES,
    ) -> None:
        self._transport = transport
        self._limit = limit

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Identity encoding makes wire bytes == decoded bytes; a transport-level
        # cap cannot bound decoded size while httpx decodes above us.
        request.headers["accept-encoding"] = "identity"

        response = await self._transport.handle_async_request(request)

        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > self._limit:
                await response.aclose()
                raise MCPResponseTooLargeError(self._limit, observed=declared)

        # Server ignored our identity request; decoding would reopen the hole.
        encoding = response.headers.get("content-encoding")
        if encoding is not None and encoding.strip().lower() not in ("", "identity"):
            await response.aclose()
            raise MCPResponseTooLargeError(self._limit)

        # Async transport always yields an async stream; guard narrows the type.
        if isinstance(response.stream, httpx.AsyncByteStream):
            response.stream = _CountingByteStream(response.stream, self._limit)
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


class OriginBoundTransport(httpx.AsyncBaseTransport):
    """Allow requests only to one configured HTTP origin."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        allowed_origin: HttpOrigin,
    ) -> None:
        self._transport = transport
        self._allowed_origin = allowed_origin

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if HttpOrigin.from_url(request.url) != self._allowed_origin:
            raise DisallowedUrlError(
                "MCP request must remain on the configured server origin"
            )
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


def create_bounded_mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    follow_redirects: bool = True,
    allowed_origin: HttpOrigin | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create a byte-capped MCP client with guarded outbound connections.

    Matches ``mcp.shared._httpx_utils.McpHttpClientFactory`` so fastmcp
    transports can install it via ``httpx_client_factory``. The extra
    ``follow_redirects`` keyword is accepted because fastmcp's HTTP transport
    passes it positionally-by-name to the factory. Additional keyword arguments
    are forwarded to ``httpx.AsyncClient``.

    Redirects are honored only when every request is bound to ``allowed_origin``;
    this preserves canonical same-origin redirects without forwarding configured
    MCP credentials to another origin. Environment proxies are always disabled.
    DNS is validated inside the TCP connection path so a hostname cannot rebind
    after a separate preflight lookup.

    ``httpx`` does not expose a public ``TypedDict`` for these constructor
    options. Keeping this passthrough typed as ``Any`` avoids coupling to
    private aliases while ``AsyncClient`` still validates safe arguments.
    """
    if timeout is None:
        timeout = httpx.Timeout(30.0, read=300.0)

    bypass_options = {"mounts", "proxy", "transport"}.intersection(kwargs)
    if bypass_options:
        names = ", ".join(sorted(bypass_options))
        raise ValueError(f"MCP HTTP client does not allow transport overrides: {names}")

    # These options belong to the transport when an explicit transport is used.
    verify = kwargs.pop("verify", True)
    cert = kwargs.pop("cert", None)
    http1 = kwargs.pop("http1", True)
    http2 = kwargs.pop("http2", False)
    limits = kwargs.pop("limits", None)
    kwargs.pop("trust_env", None)

    guarded_transport = GuardedAsyncHTTPTransport(
        configured_http_egress_policy(HttpEgressPurpose.MCP),
        verify=verify,
        cert=cert,
        http1=http1,
        http2=http2,
        limits=limits,
    )
    transport: httpx.AsyncBaseTransport = BoundedResponseTransport(guarded_transport)
    if allowed_origin is not None:
        transport = OriginBoundTransport(transport, allowed_origin)

    client = httpx.AsyncClient(
        follow_redirects=follow_redirects and allowed_origin is not None,
        timeout=timeout,
        headers=headers,
        auth=auth,
        transport=transport,
        trust_env=False,
        **kwargs,
    )
    return client
