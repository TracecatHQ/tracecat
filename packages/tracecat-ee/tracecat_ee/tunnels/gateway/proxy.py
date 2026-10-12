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
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from tracecat.logger import logger
from tracecat_ee.tunnels.schemas import TunnelTarget
from tracecat_ee.tunnels.tokens import TunnelTokenError, verify_tunnel_token

MAX_HEADER_BYTES = 16 * 1024
_BUFFER_SIZE = 64 * 1024
_TARGETS_ADAPTER = TypeAdapter(list[TunnelTarget])
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
    workspace_id: uuid.UUID
    gateway_key: str = field(repr=False)
    allowed_targets: frozenset[tuple[str, int]]
    host: str = "0.0.0.0"
    port: int = 8080
    header_timeout: float = 10.0
    connect_timeout: float = 10.0
    idle_timeout: float = 300.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> GatewayConfig:
        """Load config from ``TRACECAT_TUNNEL_*`` environment variables."""
        try:
            targets = _TARGETS_ADAPTER.validate_json(
                env["TRACECAT_TUNNEL_ALLOWED_TARGETS"]
            )
            return cls(
                tunnel_id=uuid.UUID(env["TRACECAT_TUNNEL_ID"]),
                workspace_id=uuid.UUID(env["TRACECAT_TUNNEL_WORKSPACE_ID"]),
                gateway_key=env["TRACECAT_TUNNEL_GATEWAY_KEY"],
                allowed_targets=parse_allowed_targets(targets),
                host=env.get("TRACECAT_TUNNEL_GATEWAY_HOST", "0.0.0.0"),
                port=int(env.get("TRACECAT_TUNNEL_GATEWAY_PORT", "8080")),
            )
        except KeyError as exc:
            raise ValueError(f"Missing gateway setting {exc.args[0]}") from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            raise ValueError("TRACECAT_TUNNEL_ALLOWED_TARGETS is invalid") from exc


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


class TunnelGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    async def handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        cfg = self.config
        log = logger.bind(tunnel_id=str(cfg.tunnel_id))
        upstream_writer: asyncio.StreamWriter | None = None
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
            try:
                claims = verify_tunnel_token(
                    _extract_token(request.headers),
                    gateway_key=cfg.gateway_key,
                    tunnel_id=cfg.tunnel_id,
                    workspace_id=cfg.workspace_id,
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
            if (host, port) not in cfg.allowed_targets:
                raise _ProxyError(HTTPStatus.FORBIDDEN, "target_not_allowed")

            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), cfg.connect_timeout
                )
            except TimeoutError as exc:
                raise _ProxyError(
                    HTTPStatus.GATEWAY_TIMEOUT, "upstream_timeout"
                ) from exc
            except OSError as exc:
                raise _ProxyError(
                    HTTPStatus.BAD_GATEWAY, "upstream_unreachable"
                ) from exc

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
            for writer in (upstream_writer, client_writer):
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()


async def start_gateway(config: GatewayConfig) -> asyncio.Server:
    gateway = TunnelGateway(config)
    return await asyncio.start_server(
        gateway.handle, config.host, config.port, limit=MAX_HEADER_BYTES
    )


async def serve(config: GatewayConfig) -> None:
    server = await start_gateway(config)
    logger.info(
        "Tunnel gateway listening",
        tunnel_id=str(config.tunnel_id),
        host=config.host,
        port=config.port,
        allowed_targets=sorted(f"{h}:{p}" for h, p in config.allowed_targets),
    )
    async with server:
        await server.serve_forever()
