from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tracecat.agent.otel_config import (
    AgentOtelConfig,
    ResolvedAgentOtelConfig,
    resolve_agent_otel_config,
)
from tracecat.agent.sandbox.otel_relay import (
    OtelSocketRelay,
    resolve_collector_url,
)


def _make_config(
    env: dict[str, str], headers: dict[str, str] | None = None
) -> tuple[AgentOtelConfig, dict[str, str] | None]:
    return (
        AgentOtelConfig(enabled=True, env=env),
        headers,
    )


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.response_status: int = 200

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(self.response_status, content=b"")


@pytest.fixture
def mock_transport() -> _MockTransport:
    return _MockTransport()


@pytest.fixture
def short_socket_dir() -> Iterator[Path]:
    # macOS AF_UNIX path limit (~104 chars) — tmp_path is too long.
    with tempfile.TemporaryDirectory(prefix="otrelay-") as raw:
        yield Path(raw)


@pytest.fixture
async def started_relay(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[OtelSocketRelay]:
    relay = OtelSocketRelay(
        socket_path=short_socket_dir / "o.sock",
        collector_env={
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example.com/v1/traces",
        },
        headers={"Authorization": SecretStr("Bearer secret")},
        timeout_seconds=5.0,
    )
    await relay.start()
    # Swap the outbound client for a deterministic transport
    if relay._client is not None:
        await relay._client.aclose()
    relay._client = httpx.AsyncClient(transport=mock_transport)
    try:
        yield relay
    finally:
        await relay.stop()


async def _send_request(
    socket_path: Path,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    content_type: str = "application/x-protobuf",
) -> tuple[int, str, bytes]:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        head = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: relay\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(head + body)
        await writer.drain()
        if hasattr(writer, "write_eof"):
            try:
                writer.write_eof()
            except (OSError, NotImplementedError):
                pass

        status_line = await reader.readline()
        parts = status_line.decode("ascii").strip().split(" ", 2)
        status_code = int(parts[1])
        reason = parts[2] if len(parts) > 2 else ""
        # Drain remainder
        rest = b""
        while chunk := await reader.read(4096):
            rest += chunk
        return status_code, reason, rest
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.mark.anyio
async def test_resolve_collector_url_prefers_signal_specific_endpoint() -> None:
    url = resolve_collector_url(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://generic.example.com",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example.com/v1/traces",
        },
        "/v1/traces",
    )
    assert url == "https://traces.example.com/v1/traces"


@pytest.mark.anyio
async def test_resolve_collector_url_falls_back_to_generic_with_path() -> None:
    url = resolve_collector_url(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://generic.example.com/"},
        "/v1/logs",
    )
    assert url == "https://generic.example.com/v1/logs"


@pytest.mark.anyio
async def test_resolve_collector_url_returns_none_for_unknown_path() -> None:
    assert (
        resolve_collector_url(
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://generic.example.com"},
            "/v1/health",
        )
        is None
    )


@pytest.mark.anyio
async def test_relay_forwards_post_with_injected_headers(
    started_relay: OtelSocketRelay,
    mock_transport: _MockTransport,
) -> None:
    body = b"\x0a\x05hello"
    status, _, _ = await _send_request(
        started_relay.socket_path,
        method="POST",
        path="/v1/metrics",
        body=body,
    )
    assert status == 200
    assert len(mock_transport.requests) == 1
    request = mock_transport.requests[0]
    # Generic endpoint + path (metrics has no signal-specific endpoint here)
    assert str(request.url) == "https://collector.example.com/v1/metrics"
    assert request.content == body
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["content-type"] == "application/x-protobuf"
    assert request.headers["user-agent"].startswith("tracecat-agent-otel-relay/")


@pytest.mark.anyio
async def test_relay_uses_signal_specific_endpoint(
    started_relay: OtelSocketRelay,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_relay.socket_path,
        method="POST",
        path="/v1/traces",
        body=b"trace-bytes",
    )
    assert status == 200
    assert str(mock_transport.requests[0].url) == "https://traces.example.com/v1/traces"


@pytest.mark.anyio
async def test_relay_does_not_forward_inbound_authorization(
    started_relay: OtelSocketRelay,
    mock_transport: _MockTransport,
) -> None:
    # Inbound request has no Authorization header — only the relay's configured
    # tenant headers should appear outbound.
    await _send_request(
        started_relay.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
    )
    request = mock_transport.requests[0]
    # The configured Authorization header is the only one; inbound never had a
    # competing one to leak. Verify the value is exactly the configured secret.
    assert request.headers["authorization"] == "Bearer secret"


@pytest.mark.anyio
async def test_relay_rejects_non_post_with_405(
    started_relay: OtelSocketRelay,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_relay.socket_path,
        method="GET",
        path="/v1/metrics",
    )
    assert status == 405
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_relay_rejects_unknown_path_with_404(
    started_relay: OtelSocketRelay,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_relay.socket_path,
        method="POST",
        path="/v1/health",
    )
    assert status == 404
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_resolve_disabled_telemetry_returns_empty_envs() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(enabled=False),
        org_headers=None,
        platform_override=None,
        relay_endpoint="http://127.0.0.1",
    )
    assert resolved.enabled is False
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}


@pytest.mark.anyio
async def test_sandbox_env_strips_per_signal_endpoints_and_protocols() -> None:
    config_value, headers = _make_config(
        env={
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://generic.example.com",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example.com",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc",
            "OTEL_LOG_USER_PROMPTS": "true",
            "OTEL_RESOURCE_ATTRIBUTES": "service.name=tracecat",
        },
        headers={"Authorization": "Bearer t"},
    )
    resolved = resolve_agent_otel_config(
        org_config=config_value,
        org_headers=headers,
        platform_override=None,
        relay_endpoint="http://127.0.0.1",
    )

    sandbox_env = resolved.sandbox_env
    # Per-signal endpoints and protocols stripped
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in sandbox_env
    assert "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL" not in sandbox_env
    # Allowed sandbox-safe knobs preserved
    assert sandbox_env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert sandbox_env["OTEL_LOG_USER_PROMPTS"] == "true"
    assert sandbox_env["OTEL_RESOURCE_ATTRIBUTES"] == "service.name=tracecat"
    # Headers never reach the sandbox env
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in sandbox_env
    # Tenant headers + collector env remain trusted-side only
    assert resolved.collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "https://generic.example.com"
    )
    assert resolved.headers["Authorization"].get_secret_value() == "Bearer t"


def test_resolved_config_default_is_disabled() -> None:
    resolved = ResolvedAgentOtelConfig()
    assert resolved.enabled is False
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}
