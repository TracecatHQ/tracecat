from collections.abc import AsyncIterator

import httpx
import pytest

from tracecat.agent.mcp.http_limits import (
    MCP_MAX_RESPONSE_BYTES,
    BoundedResponseTransport,
    MCPResponseTooLargeError,
    create_bounded_mcp_http_client,
)


class _AsyncStream(httpx.AsyncByteStream):
    """Async byte stream so MockTransport responses support ``await aclose()``."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_under_cap_response_passes_through_byte_identical() -> None:
    body = b"x" * 4096

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = BoundedResponseTransport(httpx.MockTransport(handler), limit=1_000_000)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("http://mcp.test/x")

    assert response.content == body


@pytest.mark.anyio
async def test_over_cap_streamed_response_raises_mid_stream() -> None:
    """No Content-Length, chunked body over the cap trips the counting stream."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_AsyncStream([b"y" * 50] * 10))

    transport = BoundedResponseTransport(httpx.MockTransport(handler), limit=100)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MCPResponseTooLargeError):
            await client.get("http://mcp.test/x")


@pytest.mark.anyio
async def test_content_length_over_cap_aborts_before_body() -> None:
    stream = _AsyncStream([b"z"])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "999999"}, stream=stream)

    transport = BoundedResponseTransport(httpx.MockTransport(handler), limit=100)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MCPResponseTooLargeError) as excinfo:
            await client.get("http://mcp.test/x")

    assert excinfo.value.observed == 999999
    assert stream.closed is True


@pytest.mark.anyio
async def test_gzip_content_encoding_is_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=_AsyncStream([b"small"])
        )

    transport = BoundedResponseTransport(httpx.MockTransport(handler), limit=1_000_000)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MCPResponseTooLargeError):
            await client.get("http://mcp.test/x")


@pytest.mark.anyio
async def test_outgoing_request_forces_identity_accept_encoding() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["accept-encoding"] = request.headers.get("accept-encoding")
        return httpx.Response(200, content=b"ok")

    transport = BoundedResponseTransport(httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://mcp.test/x")

    assert seen["accept-encoding"] == "identity"


@pytest.mark.anyio
async def test_identity_content_encoding_is_allowed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": "identity"}, content=b"ok"
        )

    transport = BoundedResponseTransport(httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("http://mcp.test/x")

    assert response.content == b"ok"


def test_factory_mirrors_mcp_defaults_and_installs_bounded_transport() -> None:
    client = create_bounded_mcp_http_client()

    assert client.follow_redirects is True
    assert client.timeout.read == 300.0
    assert isinstance(client._transport, BoundedResponseTransport)


def test_factory_accepts_follow_redirects_kwarg() -> None:
    """fastmcp's HTTP transport passes follow_redirects to the factory."""
    client = create_bounded_mcp_http_client(follow_redirects=True)

    assert client.follow_redirects is True
    assert isinstance(client._transport, BoundedResponseTransport)


def test_default_cap_is_16_mib() -> None:
    assert MCP_MAX_RESPONSE_BYTES == 16 * 1024 * 1024


def test_factory_wraps_env_proxy_mounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-derived proxy transports must also be bounded, not just _transport."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:8080")

    client = create_bounded_mcp_http_client()

    assert isinstance(client._transport, BoundedResponseTransport)
    assert client._mounts, "expected an env-derived proxy mount"
    for mount in client._mounts.values():
        if mount is not None:
            assert isinstance(mount, BoundedResponseTransport)
