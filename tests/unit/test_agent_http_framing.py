"""HTTP framing regressions at the sandbox and host socket boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest

from tracecat.agent.sandbox.llm_proxy import (
    LLMProxyError,
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
)
from tracecat.agent.sandbox.shim_entrypoint import (
    HTTP_HEADER_LIMIT,
    HTTPRequestError,
    SandboxSocketBridge,
    read_http_request,
)

REQUEST_LINE = b"POST /v1/messages HTTP/1.1\r\n"
BODY = b'{"model":"synthetic-model","max_tokens":16,"messages":[{"role":"user","content":"hello"}]}'


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def _chunked_request(body: bytes) -> bytes:
    chunks = [body[:7], body[7:]]
    return (
        REQUEST_LINE
        + b"Host: sandbox\r\nTransfer-Encoding: Chunked\r\n"
        + b"Content-Type: application/json\r\nTrailer: X-Checksum\r\n\r\n"
        + b"".join(
            f"{len(chunk):x};part=yes\r\n".encode() + chunk + b"\r\n"
            for chunk in chunks
        )
        + b"0\r\nX-Checksum: ignored\r\n\r\n"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("chunked", [False, True])
async def test_body_limit_allows_exactly_maximum(chunked: bool) -> None:
    request = (
        _chunked_request(BODY)
        if chunked
        else REQUEST_LINE + f"Content-Length: {len(BODY)}\r\n\r\n".encode() + BODY
    )
    result = await read_http_request(_reader(request), max_body_size=len(BODY))
    assert result is not None
    headers, body = result
    assert body == BODY
    assert b"transfer-encoding" not in headers.lower()
    assert b"trailer:" not in headers.lower()
    assert b"X-Checksum" not in headers
    assert f"Content-Length: {len(BODY)}\r\n".encode() in headers
    if not chunked:
        assert headers + body == request


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        (b"Content-Length: -1\r\n", b"", 400),
        (b"Content-Length: +1\r\n", b"", 400),
        (b"Content-Length: invalid\r\n", b"", 400),
        (b"Content-Length: 1, 1\r\n", b"", 400),
        (b"Content-Length: 1\r\ncontent-length: 1\r\n", b"", 400),
        (b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n", b"", 400),
        (b"Transfer-Encoding: chunked\r\ntransfer-encoding: chunked\r\n", b"", 400),
        (b"Transfer-Encoding: gzip, chunked\r\n", b"", 400),
        (b"Transfer-Encoding: \r\n", b"", 400),
        (b"Transfer-Encoding : chunked\r\n", b"", 400),
        (b"Transfer-Encoding: chunked\r\n", b"+1\r\na\r\n0\r\n\r\n", 400),
        (b"Transfer-Encoding: chunked\r\n", b"1\r\naXX", 400),
        (b"Transfer-Encoding: chunked\r\n", b"0\r\nContent-Length: 5\r\n\r\n", 400),
        (b"Content-Length: 11\r\n", b"", 413),
        (b"Content-Length: " + b"9" * 5000 + b"\r\n", b"", 413),
        (b"Transfer-Encoding: chunked\r\n", b"b\r\n", 413),
        (b"Transfer-Encoding: chunked\r\n", b"6\r\n123456\r\n5\r\n", 413),
        (b"X-Fill: a\r\n" * 7000, b"", 431),
        (b"X-Fill: " + b"a" * HTTP_HEADER_LIMIT + b"\r\n", b"", 431),
        (b"Transfer-Encoding: chunked\r\n", b"0\r\n" + b"X-Fill: a\r\n" * 7000, 431),
    ],
)
async def test_invalid_framing_is_rejected(
    headers: bytes, body: bytes, status: int
) -> None:
    with pytest.raises(HTTPRequestError) as exc:
        await read_http_request(
            _reader(REQUEST_LINE + headers + b"\r\n" + body), max_body_size=10
        )
    assert exc.value.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw_request",
    [
        REQUEST_LINE,
        REQUEST_LINE + b"Content-Length: 4\r\n\r\nab",
        REQUEST_LINE + b"Transfer-Encoding: chunked\r\n\r\n4\r\nab",
        REQUEST_LINE + b"Transfer-Encoding: chunked\r\n\r\n0\r\n",
    ],
)
async def test_truncated_request_is_never_forwardable(raw_request: bytes) -> None:
    with pytest.raises((HTTPRequestError, asyncio.IncompleteReadError)):
        await read_http_request(_reader(raw_request), max_body_size=100)


@pytest.mark.anyio
async def test_empty_request_and_empty_chunked_body() -> None:
    assert await read_http_request(_reader(b""), max_body_size=10) is None
    result = await read_http_request(
        _reader(REQUEST_LINE + b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n"),
        max_body_size=10,
    )
    assert result == (REQUEST_LINE + b"Content-Length: 0\r\n\r\n", b"")


@pytest.mark.anyio
@pytest.mark.parametrize("through_bridge", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
async def test_socket_request_reaches_gateway_intact_or_is_rejected_locally(
    through_bridge: bool, invalid: bool
) -> None:
    received: list[httpx.Request] = []
    errors: list[LLMProxyError] = []

    def handle(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, content=b"ok")

    with TemporaryDirectory() as directory:
        socket_path = Path(directory) / "llm.sock"
        proxy = LLMSocketProxy(
            socket_path,
            LLMRoutingPlan(
                managed_route=LLMRoute(
                    base_url="http://gateway",
                    model_provider="anthropic",
                    mode="managed",
                ),
                direct_routes={},
            ),
            errors.append,
        )
        # Exercise real TCP/UDS framing while mocking only the external gateway.
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            proxy._client = client
            server = await asyncio.start_unix_server(
                proxy._accept_connection, path=socket_path
            )
            bridge = SandboxSocketBridge(
                socket_path=socket_path,
                max_body_size=1024,
                on_uds_failure="error",
                log_label="Test bridge",
            )
            try:
                if through_bridge:
                    port = await bridge.start()
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                else:
                    reader, writer = await asyncio.open_unix_connection(socket_path)
                request = _chunked_request(BODY)
                if invalid:
                    request = (
                        request.replace(
                            b"Host: sandbox\r\n", b"Content-Length: 0\r\n"
                        ).split(b"\r\n\r\n", 1)[0]
                        + b"\r\n\r\n"
                    )
                try:
                    # Split headers, size lines, and payload across socket writes.
                    for offset in range(0, len(request), 3):
                        writer.write(request[offset : offset + 3])
                        await writer.drain()
                        await asyncio.sleep(0)
                    response = await asyncio.wait_for(reader.read(), timeout=3)
                finally:
                    writer.close()
                    await writer.wait_closed()
                if invalid:
                    assert response.startswith(b"HTTP/1.1 400 ")
                    assert received == []
                else:
                    assert response.startswith(b"HTTP/1.1 200 ")
                    assert len(received) == 1
                    assert received[0].content == BODY
                    assert received[0].headers["content-length"] == str(len(BODY))
                    assert "transfer-encoding" not in received[0].headers
                    assert "trailer" not in received[0].headers
                assert errors == []
            finally:
                await bridge.stop()
                server.close()
                await server.wait_closed()
                await proxy.stop()
