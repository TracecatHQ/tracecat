"""Forward proxy that fronts one Pangolin tunnel.

The gateway runs next to a Pangolin machine client (for example as a sidecar
in the same pod), so connections it opens leave through the tunnel. It only
accepts requests that carry a valid proxy token for its own tunnel and
workspace, and only to the configured ``allowed_targets``. It never falls back
to another route.

- ``https://`` targets arrive as ``CONNECT host:port``. TLS stays end to end
  between the action and the target.
- ``http://`` targets arrive as absolute-form requests
  (``GET http://host:port/path HTTP/1.1``). The gateway strips proxy and
  hop-by-hop headers, rewrites the request to origin form, and forwards it
  with ``Connection: close``, so each proxied connection carries one request
  to one checked target.

Clients pass the token either as ``Proxy-Authorization: Bearer <token>`` or as
the password of a Basic credential, which is what proxy URLs such as
``http://tracecat:<token>@gateway:8080`` send.

The gateway polls the Tracecat API for its tunnel's policy (workspace, allowed
targets, enabled state), authenticating with its gateway key. When the tunnel
is disabled or deleted, its key is rotated, or the policy can't be refreshed
for ``max_policy_age`` seconds, the gateway rejects new requests and closes
open connections. Connections to targets removed from the policy are closed too.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import functools
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from tracecat.logger import logger
from tracecat_ee.tunnels.pangolin import managed_alias
from tracecat_ee.tunnels.schemas import TunnelGatewayPolicyRead, TunnelTarget
from tracecat_ee.tunnels.tokens import (
    TunnelTokenError,
    mint_gateway_policy_token,
    verify_tunnel_token,
)

MAX_HEADER_BYTES = 16 * 1024
_BUFFER_SIZE = 64 * 1024
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
    }
)


@dataclass(frozen=True)
class GatewayConfig:
    tunnel_id: uuid.UUID
    gateway_key: str = field(repr=False)
    api_url: str = "http://localhost:8000"
    host: str = "0.0.0.0"
    port: int = 8080
    header_timeout: float = 10.0
    connect_timeout: float = 10.0
    idle_timeout: float = 300.0
    refresh_interval: float = 15.0
    max_policy_age: float = 60.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> GatewayConfig:
        """Load config from ``TRACECAT_TUNNEL_*`` environment variables."""
        try:
            return cls(
                tunnel_id=uuid.UUID(env["TRACECAT_TUNNEL_ID"]),
                gateway_key=env["TRACECAT_TUNNEL_GATEWAY_KEY"],
                api_url=env["TRACECAT_TUNNEL_API_URL"],
                host=env.get("TRACECAT_TUNNEL_GATEWAY_HOST", "0.0.0.0"),
                port=int(env.get("TRACECAT_TUNNEL_GATEWAY_PORT", "8080")),
            )
        except KeyError as exc:
            raise ValueError(f"Missing gateway setting {exc.args[0]}") from exc


@dataclass(frozen=True)
class GatewayPolicy:
    """What the gateway may currently do, as last fetched from the Tracecat API."""

    workspace_id: uuid.UUID
    allowed_targets: frozenset[tuple[str, int]]
    managed: bool = False

    def allows(self, host: str, port: int) -> bool:
        return (host, port) in self.allowed_targets


PolicyFetcher = Callable[[], Awaitable[GatewayPolicy | None]]
"""Returns the current policy, ``None`` if the gateway is revoked, or raises if
the policy could not be fetched."""


async def fetch_policy(
    config: GatewayConfig, client: httpx.AsyncClient
) -> GatewayPolicy | None:
    token = mint_gateway_policy_token(
        gateway_key=config.gateway_key, tunnel_id=config.tunnel_id
    )
    response = await client.post(
        f"{config.api_url.rstrip('/')}/tunnel-gateway/policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code in (401, 403, 404):
        return None
    response.raise_for_status()
    data = TunnelGatewayPolicyRead.model_validate_json(response.content)
    if data.tunnel_id != config.tunnel_id:
        raise ValueError("Tunnel policy is for a different tunnel")
    return GatewayPolicy(
        workspace_id=data.workspace_id,
        allowed_targets=parse_allowed_targets(data.allowed_targets),
        managed=data.managed,
    )


def parse_allowed_targets(targets: list[TunnelTarget]) -> frozenset[tuple[str, int]]:
    if not targets:
        raise ValueError("At least one allowed target is required")
    return frozenset((t.host, port) for t in targets for port in t.ports)


class _ProxyError(Exception):
    def __init__(self, status: HTTPStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _normalize_target(host: str, port: int) -> tuple[str, int]:
    try:
        target = TunnelTarget(host=host, ports=[port])
    except ValidationError as exc:
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "invalid_target") from exc
    return target.host, target.ports[0]


def _parse_authority(authority: str) -> tuple[str, int]:
    host, sep, port_str = authority.rpartition(":")
    if not sep or not port_str.isdigit():
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "invalid_target")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return _normalize_target(host, int(port_str))


@dataclass(frozen=True)
class _ParsedRequest:
    method: str
    target: str
    version: str
    header_items: list[tuple[str, str]]

    @property
    def headers(self) -> dict[str, str]:
        return {name.lower(): value for name, value in self.header_items}


def _http_forward(request: _ParsedRequest) -> tuple[str, int, bytes]:
    """Validate an absolute-form ``http://`` request and rewrite it to origin form."""
    url = urlsplit(request.target)
    if url.scheme.lower() != "http" or not url.hostname:
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "absolute_http_uri_required")
    if url.username or url.password or url.fragment:
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "invalid_target")
    try:
        port = url.port or 80
    except ValueError as exc:
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "invalid_target") from exc
    host, port = _normalize_target(url.hostname, port)

    connection_tokens = {
        token.strip().lower()
        for name, value in request.header_items
        if name.lower() == "connection"
        for token in value.split(",")
    }
    path = url.path or "/"
    if url.query:
        path = f"{path}?{url.query}"
    lines = [f"{request.method} {path} {request.version}", f"Host: {url.netloc}"]
    lines.extend(
        f"{name}: {value}"
        for name, value in request.header_items
        if name.lower() not in _HOP_BY_HOP_HEADERS
        and name.lower() not in connection_tokens
    )
    lines.append("Connection: close")
    return host, port, ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _extract_token(headers: Mapping[str, str]) -> str:
    value = headers.get("proxy-authorization")
    if not value:
        raise _ProxyError(HTTPStatus.PROXY_AUTHENTICATION_REQUIRED, "missing_token")
    scheme, _, credentials = value.strip().partition(" ")
    credentials = credentials.strip()
    match scheme.lower():
        case "bearer":
            token = credentials
        case "basic":
            try:
                decoded = base64.b64decode(credentials, validate=True).decode()
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise _ProxyError(
                    HTTPStatus.PROXY_AUTHENTICATION_REQUIRED, "invalid_token"
                ) from exc
            token = decoded.partition(":")[2]
        case _:
            token = ""
    if not token:
        raise _ProxyError(HTTPStatus.PROXY_AUTHENTICATION_REQUIRED, "invalid_token")
    return token


def _parse_head(head: bytes) -> _ParsedRequest:
    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ")
    except ValueError as exc:
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "malformed_request") from exc
    if version not in ("HTTP/1.0", "HTTP/1.1") or not method.isalpha():
        raise _ProxyError(HTTPStatus.BAD_REQUEST, "malformed_request")
    header_items: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        name = name.strip()
        if not sep or not name or " " in name:
            raise _ProxyError(HTTPStatus.BAD_REQUEST, "malformed_request")
        header_items.append((name, value.strip()))
    return _ParsedRequest(method.upper(), target, version, header_items)


async def _respond(writer: asyncio.StreamWriter, status: HTTPStatus) -> None:
    extra = (
        'Proxy-Authenticate: Bearer realm="tracecat-tunnel"\r\n'
        if status is HTTPStatus.PROXY_AUTHENTICATION_REQUIRED
        else ""
    )
    writer.write(
        f"HTTP/1.1 {status.value} {status.phrase}\r\n{extra}"
        "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
    )
    with contextlib.suppress(ConnectionError):
        await writer.drain()


async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    idle_timeout: float,
) -> int:
    total = 0
    try:
        while data := await asyncio.wait_for(reader.read(_BUFFER_SIZE), idle_timeout):
            writer.write(data)
            await writer.drain()
            total += len(data)
    except (ConnectionError, TimeoutError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()
    return total


@dataclass(eq=False)
class _OpenConnection:
    workspace_id: uuid.UUID
    host: str
    port: int
    writers: tuple[asyncio.StreamWriter, ...]

    def close(self) -> None:
        for writer in self.writers:
            writer.close()


class TunnelGateway:
    def __init__(self, config: GatewayConfig, fetch: PolicyFetcher) -> None:
        self.config = config
        self._fetch = fetch
        self._policy: GatewayPolicy | None = None
        self._policy_fetched_at: float | None = None
        self._connections: set[_OpenConnection] = set()

    def current_policy(self) -> GatewayPolicy:
        """Return the live policy, failing closed if it is revoked or stale."""
        fetched_at = self._policy_fetched_at
        if (
            fetched_at is None
            or time.monotonic() - fetched_at > self.config.max_policy_age
        ):
            raise _ProxyError(HTTPStatus.SERVICE_UNAVAILABLE, "policy_unavailable")
        if self._policy is None:
            raise _ProxyError(HTTPStatus.FORBIDDEN, "tunnel_revoked")
        return self._policy

    async def refresh(self) -> None:
        log = logger.bind(tunnel_id=str(self.config.tunnel_id))
        try:
            policy = await self._fetch()
        except Exception as exc:
            log.warning("Failed to refresh tunnel policy", error=type(exc).__name__)
        else:
            if policy is None and self._policy is not None:
                log.warning("Tunnel gateway revoked")
            self._policy = policy
            self._policy_fetched_at = time.monotonic()
        self._close_disallowed_connections()

    async def run_refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.refresh_interval)
            await self.refresh()

    def _permits(self, conn: _OpenConnection) -> bool:
        try:
            policy = self.current_policy()
        except _ProxyError:
            return False
        return policy.workspace_id == conn.workspace_id and policy.allows(
            conn.host, conn.port
        )

    def _close_disallowed_connections(self) -> None:
        for conn in list(self._connections):
            if not self._permits(conn):
                logger.warning(
                    "Closing tunnel connection no longer allowed",
                    tunnel_id=str(self.config.tunnel_id),
                    target_host=conn.host,
                    target_port=conn.port,
                )
                conn.close()

    async def handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        cfg = self.config
        log = logger.bind(tunnel_id=str(cfg.tunnel_id))
        upstream_writer: asyncio.StreamWriter | None = None
        conn: _OpenConnection | None = None
        try:
            try:
                head = await asyncio.wait_for(
                    client_reader.readuntil(b"\r\n\r\n"), cfg.header_timeout
                )
            except asyncio.LimitOverrunError as exc:
                raise _ProxyError(
                    HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, "headers_too_large"
                ) from exc
            except (asyncio.IncompleteReadError, TimeoutError) as exc:
                raise _ProxyError(HTTPStatus.BAD_REQUEST, "incomplete_request") from exc

            request = _parse_head(head)
            forward_head: bytes | None = None
            if request.method == "CONNECT":
                host, port = _parse_authority(request.target)
            else:
                host, port, forward_head = _http_forward(request)
            policy = self.current_policy()
            try:
                claims = verify_tunnel_token(
                    _extract_token(request.headers),
                    gateway_key=cfg.gateway_key,
                    tunnel_id=cfg.tunnel_id,
                    workspace_id=policy.workspace_id,
                )
            except TunnelTokenError as exc:
                raise _ProxyError(
                    HTTPStatus.PROXY_AUTHENTICATION_REQUIRED, "invalid_token"
                ) from exc
            log = log.bind(
                method=request.method,
                wf_exec_id=claims.wf_exec_id,
                action_ref=claims.action_ref,
                target_host=host,
                target_port=port,
            )
            if not policy.allows(host, port):
                raise _ProxyError(HTTPStatus.FORBIDDEN, "target_not_allowed")

            dial_host = host
            if policy.managed:
                dial_host = managed_alias(cfg.tunnel_id, host) or host
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(dial_host, port),
                    cfg.connect_timeout,
                )
            except TimeoutError as exc:
                raise _ProxyError(
                    HTTPStatus.GATEWAY_TIMEOUT, "upstream_timeout"
                ) from exc
            except OSError as exc:
                raise _ProxyError(
                    HTTPStatus.BAD_GATEWAY, "upstream_unreachable"
                ) from exc

            conn = _OpenConnection(
                policy.workspace_id, host, port, (client_writer, upstream_writer)
            )
            self._connections.add(conn)
            # The policy may have changed while the upstream connection opened.
            if not self._permits(conn):
                raise _ProxyError(HTTPStatus.FORBIDDEN, "tunnel_revoked")

            if forward_head is None:
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            else:
                upstream_writer.write(forward_head)
                await upstream_writer.drain()
            log.info("Tunnel connection opened")
            sent, received = await asyncio.gather(
                _pipe(client_reader, upstream_writer, cfg.idle_timeout),
                _pipe(upstream_reader, client_writer, cfg.idle_timeout),
            )
            log.info(
                "Tunnel connection closed", bytes_sent=sent, bytes_received=received
            )
        except _ProxyError as exc:
            log.warning(
                "Tunnel connection rejected",
                status=exc.status.value,
                reason=exc.reason,
            )
            await _respond(client_writer, exc.status)
        except ConnectionError:
            pass
        finally:
            if conn is not None:
                self._connections.discard(conn)
            for writer in (upstream_writer, client_writer):
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()


async def start_gateway(
    config: GatewayConfig, fetch: PolicyFetcher
) -> tuple[asyncio.Server, TunnelGateway]:
    """Load the tunnel policy and start listening. Fails if no policy loads."""
    gateway = TunnelGateway(config, fetch)
    await gateway.refresh()
    try:
        gateway.current_policy()
    except _ProxyError as exc:
        raise RuntimeError(f"Tunnel gateway cannot start: {exc.reason}") from exc
    server = await asyncio.start_server(
        gateway.handle, config.host, config.port, limit=MAX_HEADER_BYTES
    )
    return server, gateway


async def serve(config: GatewayConfig) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        server, gateway = await start_gateway(
            config, functools.partial(fetch_policy, config, client)
        )
        logger.info(
            "Tunnel gateway listening",
            tunnel_id=str(config.tunnel_id),
            host=config.host,
            port=config.port,
        )
        refresh_task = asyncio.create_task(gateway.run_refresh_loop())
        try:
            async with server:
                await server.serve_forever()
        finally:
            refresh_task.cancel()
