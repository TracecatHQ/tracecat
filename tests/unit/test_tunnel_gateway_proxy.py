import asyncio
import base64
import json
import uuid
from collections.abc import AsyncGenerator

import pytest
from tracecat_ee.tunnels.gateway.proxy import (
    GatewayConfig,
    start_gateway,
)
from tracecat_ee.tunnels.tokens import generate_gateway_key, mint_tunnel_token

pytestmark = pytest.mark.anyio

KEY = generate_gateway_key()
TUNNEL_ID = uuid.uuid4()
WORKSPACE_ID = uuid.uuid4()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(1024):
        writer.write(data)
        await writer.drain()
    writer.close()


@pytest.fixture
async def upstream_port() -> AsyncGenerator[int, None]:
    server = await asyncio.start_server(_echo, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


@pytest.fixture
async def gateway_port(upstream_port: int) -> AsyncGenerator[int, None]:
    config = GatewayConfig(
        tunnel_id=TUNNEL_ID,
        workspace_id=WORKSPACE_ID,
        gateway_key=KEY,
        allowed_targets=frozenset({("127.0.0.1", upstream_port)}),
        host="127.0.0.1",
        port=0,
        header_timeout=1.0,
        connect_timeout=1.0,
        idle_timeout=2.0,
    )
    server = await start_gateway(config)
    async with server:
        yield server.sockets[0].getsockname()[1]


def _token(**overrides: object) -> str:
    params: dict[str, object] = {
        "gateway_key": KEY,
        "tunnel_id": TUNNEL_ID,
        "workspace_id": WORKSPACE_ID,
        "wf_exec_id": "wf-exec-1",
    }
    params.update(overrides)
    return mint_tunnel_token(**params)  # pyright: ignore[reportArgumentType]


async def _request(
    gateway_port: int, request: bytes
) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway_port)
    writer.write(request)
    await writer.drain()
    status = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
    return status, reader, writer


def _connect(target: str, auth: str | None) -> bytes:
    headers = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
    if auth is not None:
        headers += f"Proxy-Authorization: {auth}\r\n"
    return (headers + "\r\n").encode()


async def test_bearer_token_tunnels_bytes(
    gateway_port: int, upstream_port: int
) -> None:
    status, reader, writer = await _request(
        gateway_port, _connect(f"127.0.0.1:{upstream_port}", f"Bearer {_token()}")
    )
    assert status.startswith(b"HTTP/1.1 200")
    writer.write(b"ping")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(4), 3) == b"ping"
    writer.close()


async def test_basic_credentials_from_proxy_url(
    gateway_port: int, upstream_port: int
) -> None:
    basic = base64.b64encode(f"tracecat:{_token()}".encode()).decode()
    status, _, writer = await _request(
        gateway_port, _connect(f"127.0.0.1:{upstream_port}", f"Basic {basic}")
    )
    assert status.startswith(b"HTTP/1.1 200")
    writer.close()


@pytest.mark.parametrize(
    ("auth", "expected"),
    [
        (None, b"407"),
        ("Bearer not-a-jwt", b"407"),
        ("Basic !!!", b"407"),
        ("Digest abc", b"407"),
    ],
)
async def test_rejects_missing_or_bad_tokens(
    gateway_port: int, upstream_port: int, auth: str | None, expected: bytes
) -> None:
    status, _, writer = await _request(
        gateway_port, _connect(f"127.0.0.1:{upstream_port}", auth)
    )
    assert status.split(b" ")[1] == expected
    assert b"Proxy-Authenticate" in status
    writer.close()


@pytest.mark.parametrize(
    "token_overrides",
    [
        {"tunnel_id": uuid.uuid4()},
        {"workspace_id": uuid.uuid4()},
        {"gateway_key": generate_gateway_key()},
    ],
)
async def test_rejects_tokens_for_other_tunnels(
    gateway_port: int, upstream_port: int, token_overrides: dict[str, object]
) -> None:
    status, _, writer = await _request(
        gateway_port,
        _connect(f"127.0.0.1:{upstream_port}", f"Bearer {_token(**token_overrides)}"),
    )
    assert status.startswith(b"HTTP/1.1 407")
    writer.close()


@pytest.mark.parametrize(
    "target_fmt",
    ["127.0.0.1:{other}", "localhost:{port}", "10.0.0.1:{port}"],
)
async def test_rejects_targets_outside_allowlist(
    gateway_port: int, upstream_port: int, target_fmt: str
) -> None:
    target = target_fmt.format(port=upstream_port, other=upstream_port + 1)
    status, _, writer = await _request(
        gateway_port, _connect(target, f"Bearer {_token()}")
    )
    assert status.startswith(b"HTTP/1.1 403")
    writer.close()


async def test_rejects_non_connect_methods(
    gateway_port: int, upstream_port: int
) -> None:
    request = (
        f"GET http://127.0.0.1:{upstream_port}/ HTTP/1.1\r\n"
        f"Proxy-Authorization: Bearer {_token()}\r\n\r\n"
    ).encode()
    status, _, writer = await _request(gateway_port, request)
    assert status.startswith(b"HTTP/1.1 405")
    writer.close()


@pytest.mark.parametrize("target", ["127.0.0.1", "*.corp.example:443", "host:abc"])
async def test_rejects_malformed_targets(gateway_port: int, target: str) -> None:
    status, _, writer = await _request(
        gateway_port, _connect(target, f"Bearer {_token()}")
    )
    assert status.startswith(b"HTTP/1.1 400")
    writer.close()


async def test_rejects_oversized_headers(gateway_port: int) -> None:
    request = b"CONNECT a:1 HTTP/1.1\r\nX-Pad: " + b"a" * 20_000 + b"\r\n\r\n"
    status, _, writer = await _request(gateway_port, request)
    assert status.startswith(b"HTTP/1.1 431")
    writer.close()


async def test_unreachable_upstream_returns_bad_gateway(upstream_port: int) -> None:
    closed_port = upstream_port + 1
    config = GatewayConfig(
        tunnel_id=TUNNEL_ID,
        workspace_id=WORKSPACE_ID,
        gateway_key=KEY,
        allowed_targets=frozenset({("127.0.0.1", closed_port)}),
        host="127.0.0.1",
        port=0,
    )
    server = await start_gateway(config)
    async with server:
        port = server.sockets[0].getsockname()[1]
        status, _, writer = await _request(
            port, _connect(f"127.0.0.1:{closed_port}", f"Bearer {_token()}")
        )
    assert status.startswith(b"HTTP/1.1 502")
    writer.close()


def test_config_from_env() -> None:
    config = GatewayConfig.from_env(
        {
            "TRACECAT_TUNNEL_ID": str(TUNNEL_ID),
            "TRACECAT_TUNNEL_WORKSPACE_ID": str(WORKSPACE_ID),
            "TRACECAT_TUNNEL_GATEWAY_KEY": KEY,
            "TRACECAT_TUNNEL_ALLOWED_TARGETS": json.dumps(
                [{"host": "Splunk.Corp.Example", "ports": [8089, 443]}]
            ),
        }
    )
    assert config.allowed_targets == frozenset(
        {("splunk.corp.example", 8089), ("splunk.corp.example", 443)}
    )
    assert config.port == 8080
    assert KEY not in repr(config)


@pytest.mark.parametrize(
    "targets", ["[]", "not json", '[{"host": "*.corp", "ports": [443]}]']
)
def test_config_rejects_invalid_targets(targets: str) -> None:
    with pytest.raises(ValueError):
        GatewayConfig.from_env(
            {
                "TRACECAT_TUNNEL_ID": str(TUNNEL_ID),
                "TRACECAT_TUNNEL_WORKSPACE_ID": str(WORKSPACE_ID),
                "TRACECAT_TUNNEL_GATEWAY_KEY": KEY,
                "TRACECAT_TUNNEL_ALLOWED_TARGETS": targets,
            }
        )


def test_config_requires_settings() -> None:
    with pytest.raises(ValueError, match="TRACECAT_TUNNEL_GATEWAY_KEY"):
        GatewayConfig.from_env(
            {
                "TRACECAT_TUNNEL_ID": str(TUNNEL_ID),
                "TRACECAT_TUNNEL_WORKSPACE_ID": str(WORKSPACE_ID),
                "TRACECAT_TUNNEL_ALLOWED_TARGETS": "[]",
            }
        )
