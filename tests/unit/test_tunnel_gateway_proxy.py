import asyncio
import base64
import ipaddress
import json
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest
import tracecat_ee.tunnels.gateway.proxy as proxy_module
from tracecat_ee.tunnels.gateway.proxy import (
    GatewayConfig,
    GatewayPolicy,
    PolicyFetcher,
    TunnelGateway,
    fetch_policy,
    start_gateway,
)
from tracecat_ee.tunnels.pangolin import managed_alias
from tracecat_ee.tunnels.tokens import (
    generate_gateway_key,
    mint_tunnel_token,
    verify_gateway_policy_token,
)

pytestmark = pytest.mark.anyio

KEY = generate_gateway_key()
TUNNEL_ID = uuid.uuid4()
WORKSPACE_ID = uuid.uuid4()


def _policy(*targets: tuple[str, int], managed: bool = False) -> GatewayPolicy:
    return GatewayPolicy(
        workspace_id=WORKSPACE_ID,
        allowed_targets=frozenset(targets),
        managed=managed,
    )


class _MutablePolicy:
    """Policy source a test can change, revoke, or break between refreshes."""

    def __init__(self, policy: GatewayPolicy | None) -> None:
        self.policy = policy
        self.error: Exception | None = None

    async def __call__(self) -> GatewayPolicy | None:
        if self.error is not None:
            raise self.error
        return self.policy


def _static(*targets: tuple[str, int], managed: bool = False) -> PolicyFetcher:
    return _MutablePolicy(_policy(*targets, managed=managed))


_REAL_REFRESH_API_ADDRESSES = TunnelGateway._refresh_api_addresses


@pytest.fixture(autouse=True)
def skip_api_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: TunnelGateway) -> None:
        return None

    monkeypatch.setattr(TunnelGateway, "_refresh_api_addresses", _noop)


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
        blocked_networks=(),
        tunnel_id=TUNNEL_ID,
        gateway_key=KEY,
        host="127.0.0.1",
        port=0,
        header_timeout=1.0,
        connect_timeout=1.0,
        idle_timeout=2.0,
    )
    server, _ = await start_gateway(config, _static(("127.0.0.1", upstream_port)))
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


async def test_rejects_origin_form_requests(gateway_port: int) -> None:
    request = f"GET /search HTTP/1.1\r\nProxy-Authorization: Bearer {_token()}\r\n\r\n"
    status, _, writer = await _request(gateway_port, request.encode())
    assert status.startswith(b"HTTP/1.1 400")
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
        blocked_networks=(),
        tunnel_id=TUNNEL_ID,
        gateway_key=KEY,
        host="127.0.0.1",
        port=0,
    )
    server, _ = await start_gateway(config, _static(("127.0.0.1", closed_port)))
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
            "TRACECAT_TUNNEL_GATEWAY_KEY": KEY,
            "TRACECAT_TUNNEL_API_URL": "http://api:8000",
        }
    )
    assert config.api_url == "http://api:8000"
    assert config.port == 8080
    assert KEY not in repr(config)


def test_config_requires_settings() -> None:
    with pytest.raises(ValueError, match="TRACECAT_TUNNEL_API_URL"):
        GatewayConfig.from_env(
            {"TRACECAT_TUNNEL_ID": str(TUNNEL_ID), "TRACECAT_TUNNEL_GATEWAY_KEY": KEY}
        )


async def _http_upstream(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:] if line)
    length = int(headers.get("Content-Length", "0"))
    body = await reader.readexactly(length) if length else b""
    payload = json.dumps(
        {"request_line": lines[0], "headers": headers, "body": body.decode()}
    ).encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
        + payload
    )
    await writer.drain()
    writer.close()


@pytest.fixture
async def http_upstream_port() -> AsyncGenerator[int, None]:
    server = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


@pytest.fixture
async def http_gateway_port(http_upstream_port: int) -> AsyncGenerator[int, None]:
    config = GatewayConfig(
        blocked_networks=(),
        tunnel_id=TUNNEL_ID,
        gateway_key=KEY,
        host="127.0.0.1",
        port=0,
    )
    server, _ = await start_gateway(config, _static(("127.0.0.1", http_upstream_port)))
    async with server:
        yield server.sockets[0].getsockname()[1]


async def test_http_client_forwards_plain_http_requests(
    http_gateway_port: int, http_upstream_port: int
) -> None:
    proxy = f"http://tracecat:{_token()}@127.0.0.1:{http_gateway_port}"
    async with httpx.AsyncClient(proxy=proxy) as client:
        response = await client.post(
            f"http://127.0.0.1:{http_upstream_port}/services/search/jobs?output_mode=json",
            content=b"search=index%3Dmain",
            headers={"Authorization": "Bearer splunk-token", "Connection": "X-Drop"},
        )
    assert response.status_code == 200
    seen = response.json()
    assert seen["request_line"] == (
        "POST /services/search/jobs?output_mode=json HTTP/1.1"
    )
    assert seen["headers"]["Host"] == f"127.0.0.1:{http_upstream_port}"
    assert seen["headers"]["Authorization"] == "Bearer splunk-token"
    assert seen["headers"]["Connection"] == "close"
    assert "Proxy-Authorization" not in seen["headers"]
    assert seen["body"] == "search=index%3Dmain"


async def test_http_forward_rejects_bad_token_and_targets(
    http_gateway_port: int, http_upstream_port: int
) -> None:
    bad_proxy = f"http://tracecat:{_token(tunnel_id=uuid.uuid4())}@127.0.0.1:{http_gateway_port}"
    async with httpx.AsyncClient(proxy=bad_proxy) as client:
        response = await client.get(f"http://127.0.0.1:{http_upstream_port}/")
    assert response.status_code == 407

    proxy = f"http://tracecat:{_token()}@127.0.0.1:{http_gateway_port}"
    async with httpx.AsyncClient(proxy=proxy) as client:
        other_port = await client.get(f"http://127.0.0.1:{http_upstream_port + 1}/")
        other_host = await client.get(f"http://localhost:{http_upstream_port}/")
    assert other_port.status_code == 403
    assert other_host.status_code == 403


@pytest.mark.parametrize(
    "target",
    [
        "ftp://127.0.0.1:21/file",
        "http://user:pass@127.0.0.1:{port}/",
        "http://127.0.0.1:notaport/",
        "http:///nohost",
    ],
)
async def test_http_forward_rejects_malformed_uris(
    http_gateway_port: int, http_upstream_port: int, target: str
) -> None:
    request = (
        f"GET {target.format(port=http_upstream_port)} HTTP/1.1\r\n"
        f"Proxy-Authorization: Bearer {_token()}\r\n\r\n"
    )
    status, _, writer = await _request(http_gateway_port, request.encode())
    assert status.startswith(b"HTTP/1.1 400")
    writer.close()


@pytest.mark.parametrize(
    ("managed", "host", "uses_alias"),
    [
        (True, "splunk.corp", True),
        (True, "10.0.0.5", False),
        (False, "splunk.corp", False),
    ],
)
async def test_managed_gateway_dials_per_tunnel_alias(
    monkeypatch: pytest.MonkeyPatch, managed: bool, host: str, uses_alias: bool
) -> None:
    expected = managed_alias(TUNNEL_ID, host) if uses_alias else host
    dialed: list[str] = []

    async def _resolve(dial_host: str, port: int) -> list[ipaddress.IPv4Address]:
        dialed.append(dial_host)
        return [ipaddress.IPv4Address("10.0.0.9")]

    async def _open_connection(dial_host: str, port: int) -> None:
        raise OSError("unreachable")

    monkeypatch.setattr(proxy_module, "_resolve", _resolve)
    monkeypatch.setattr(asyncio, "open_connection", _open_connection)
    gateway = TunnelGateway(
        GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY, blocked_networks=()),
        _static((host, 8089), managed=managed),
    )
    await gateway.refresh()
    reader = asyncio.StreamReader()
    reader.feed_data(_connect(f"{host}:8089", f"Bearer {_token()}"))
    writer = AsyncMock()
    writer.write = lambda _data: None
    writer.close = lambda: None
    await gateway.handle(reader, writer)
    assert dialed == [expected]


async def _revocable_gateway(
    upstream_port: int, max_policy_age: float = 60.0
) -> tuple[asyncio.Server, TunnelGateway, _MutablePolicy]:
    source = _MutablePolicy(_policy(("127.0.0.1", upstream_port)))
    server, gateway = await start_gateway(
        GatewayConfig(
            blocked_networks=(),
            tunnel_id=TUNNEL_ID,
            gateway_key=KEY,
            host="127.0.0.1",
            port=0,
            max_policy_age=max_policy_age,
        ),
        source,
    )
    return server, gateway, source


async def _assert_closed(reader: asyncio.StreamReader) -> None:
    assert await asyncio.wait_for(reader.read(1024), 3) == b""


@pytest.mark.parametrize("change", ["revoke", "remove_target", "other_workspace"])
async def test_policy_change_closes_open_connections_and_rejects_new(
    upstream_port: int, change: str
) -> None:
    server, gateway, source = await _revocable_gateway(upstream_port)
    async with server:
        port = server.sockets[0].getsockname()[1]
        target = f"127.0.0.1:{upstream_port}"
        status, reader, writer = await _request(
            port, _connect(target, f"Bearer {_token()}")
        )
        assert status.startswith(b"HTTP/1.1 200")

        if change == "revoke":
            source.policy = None
        elif change == "remove_target":
            source.policy = _policy(("127.0.0.1", upstream_port + 1))
        else:
            source.policy = GatewayPolicy(
                workspace_id=uuid.uuid4(),
                allowed_targets=frozenset({("127.0.0.1", upstream_port)}),
            )
        await gateway.refresh()
        await _assert_closed(reader)
        writer.close()

        status, _, writer = await _request(port, _connect(target, f"Bearer {_token()}"))
        assert status.split(b" ")[1] in {b"403", b"407"}
        writer.close()


async def test_stale_policy_fails_closed(upstream_port: int) -> None:
    server, gateway, source = await _revocable_gateway(
        upstream_port, max_policy_age=0.2
    )
    async with server:
        port = server.sockets[0].getsockname()[1]
        target = f"127.0.0.1:{upstream_port}"
        status, reader, writer = await _request(
            port, _connect(target, f"Bearer {_token()}")
        )
        assert status.startswith(b"HTTP/1.1 200")

        source.error = httpx.ConnectError("api down")
        await gateway.refresh()
        writer.write(b"still-open")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(1024), 3) == b"still-open"

        await asyncio.sleep(0.3)
        await gateway.refresh()
        await _assert_closed(reader)
        writer.close()

        status, _, writer = await _request(port, _connect(target, f"Bearer {_token()}"))
        assert status.startswith(b"HTTP/1.1 503")
        writer.close()

        source.error = None
        await gateway.refresh()
        status, _, writer = await _request(port, _connect(target, f"Bearer {_token()}"))
        assert status.startswith(b"HTTP/1.1 200")
        writer.close()


@pytest.mark.parametrize("policy", [None, "error"])
async def test_gateway_refuses_to_start_without_policy(policy: str | None) -> None:
    source = _MutablePolicy(None)
    if policy == "error":
        source.error = httpx.ConnectError("api down")
    with pytest.raises(RuntimeError):
        await start_gateway(
            GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY, port=0), source
        )


def _policy_client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # pyright: ignore[reportArgumentType]


async def test_fetch_policy_signs_request_and_parses_policy() -> None:
    config = GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY, api_url="http://api/")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://api/tunnel-gateway/policy"
        token = request.headers["authorization"].removeprefix("Bearer ")
        verify_gateway_policy_token(token, gateway_key=KEY, tunnel_id=TUNNEL_ID)
        return httpx.Response(
            200,
            json={
                "tunnel_id": str(TUNNEL_ID),
                "workspace_id": str(WORKSPACE_ID),
                "allowed_targets": [{"host": "Splunk.Corp", "ports": [8089]}],
                "managed": True,
            },
        )

    async with _policy_client(handler) as client:
        policy = await fetch_policy(config, client)
    assert policy == _policy(("splunk.corp", 8089), managed=True)


@pytest.mark.parametrize("status_code", [401, 403, 404])
async def test_fetch_policy_revoked_statuses_return_none(status_code: int) -> None:
    config = GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY, blocked_networks=())
    async with _policy_client(lambda _req: httpx.Response(status_code)) as client:
        assert await fetch_policy(config, client) is None


async def test_fetch_policy_rejects_other_tunnels_policy() -> None:
    config = GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY, blocked_networks=())
    body = {
        "tunnel_id": str(uuid.uuid4()),
        "workspace_id": str(WORKSPACE_ID),
        "allowed_targets": [{"host": "splunk.corp", "ports": [8089]}],
        "managed": False,
    }
    async with _policy_client(lambda _req: httpx.Response(200, json=body)) as client:
        with pytest.raises(ValueError):
            await fetch_policy(config, client)


async def _handle_connect(gateway: TunnelGateway, target: str) -> bytes:
    await gateway.refresh()
    reader = asyncio.StreamReader()
    reader.feed_data(_connect(target, f"Bearer {_token()}"))
    written: list[bytes] = []
    writer = AsyncMock()
    writer.write = written.append
    writer.close = lambda: None
    await gateway.handle(reader, writer)
    return b"".join(written)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("host", "resolved"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("::ffff:127.0.0.1", "::ffff:127.0.0.1"),
        ("splunk.corp", "169.254.169.254"),
        ("splunk.corp", "fd00:ec2::254"),
        ("splunk.corp", "0.0.0.0"),
    ],
)
async def test_blocks_internal_destinations_after_resolution(
    monkeypatch: pytest.MonkeyPatch, host: str, resolved: str
) -> None:
    dialed: list[str] = []

    async def _resolve(dial_host: str, port: int) -> list[ipaddress._BaseAddress]:
        return [ipaddress.ip_address(resolved)]

    async def _open_connection(dial_host: str, port: int) -> None:
        dialed.append(dial_host)
        raise OSError("unreachable")

    monkeypatch.setattr(proxy_module, "_resolve", _resolve)
    monkeypatch.setattr(asyncio, "open_connection", _open_connection)
    gateway = TunnelGateway(
        GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY),
        _static((host, 8089)),
    )
    response = await _handle_connect(gateway, f"{host}:8089")
    assert response.startswith(b"HTTP/1.1 403")
    assert dialed == []


@pytest.mark.anyio
async def test_blocks_the_tracecat_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TunnelGateway, "_refresh_api_addresses", _REAL_REFRESH_API_ADDRESSES
    )
    dialed: list[str] = []

    async def _resolve(dial_host: str, port: int) -> list[ipaddress.IPv4Address]:
        return [ipaddress.IPv4Address("10.1.2.3")]

    async def _open_connection(dial_host: str, port: int) -> None:
        dialed.append(dial_host)
        raise OSError("unreachable")

    monkeypatch.setattr(proxy_module, "_resolve", _resolve)
    monkeypatch.setattr(asyncio, "open_connection", _open_connection)
    gateway = TunnelGateway(
        GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY, api_url="http://api:8000"),
        _static(("api.internal", 8000)),
    )
    response = await _handle_connect(gateway, "api.internal:8000")
    assert response.startswith(b"HTTP/1.1 403")
    assert dialed == []


@pytest.mark.anyio
async def test_dials_resolved_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    dialed: list[str] = []

    async def _resolve(dial_host: str, port: int) -> list[ipaddress.IPv4Address]:
        return [ipaddress.IPv4Address("10.20.0.15")]

    async def _open_connection(dial_host: str, port: int) -> None:
        dialed.append(dial_host)
        raise OSError("unreachable")

    monkeypatch.setattr(proxy_module, "_resolve", _resolve)
    monkeypatch.setattr(asyncio, "open_connection", _open_connection)
    gateway = TunnelGateway(
        GatewayConfig(tunnel_id=TUNNEL_ID, gateway_key=KEY),
        _static(("splunk.corp", 8089)),
    )
    response = await _handle_connect(gateway, "splunk.corp:8089")
    assert response.startswith(b"HTTP/1.1 502")
    assert dialed == ["10.20.0.15"]


def test_config_reads_blocked_cidrs() -> None:
    config = GatewayConfig.from_env(
        {
            "TRACECAT_TUNNEL_ID": str(TUNNEL_ID),
            "TRACECAT_TUNNEL_GATEWAY_KEY": KEY,
            "TRACECAT_TUNNEL_API_URL": "http://api:8000",
            "TRACECAT_TUNNEL_GATEWAY_BLOCKED_CIDRS": "10.96.0.0/12, 172.20.0.0/16",
        }
    )
    assert ipaddress.ip_network("10.96.0.0/12") in config.blocked_networks
    assert ipaddress.ip_network("172.20.0.0/16") in config.blocked_networks
    assert ipaddress.ip_network("127.0.0.0/8") in config.blocked_networks
