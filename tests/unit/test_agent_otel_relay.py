from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from pydantic import HttpUrl, SecretStr

from tracecat import config
from tracecat.agent.otel_config import (
    AgentOtelConfig,
    ResolvedAgentOtelConfig,
    resolve_agent_otel_config,
)
from tracecat.agent.sandbox import otel_relay
from tracecat.agent.sandbox.otel_relay import (
    MAX_BODY_SIZE,
    OtelSocketReceiver,
    resolve_collector_url,
)
from tracecat.agent.tokens import mint_agent_otel_token


@dataclass(frozen=True, slots=True)
class _ReceiverIdentity:
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    token: str


class _ExplodingStream(httpx.AsyncByteStream):
    """Response body that fails loudly if anything tries to consume it."""

    def __init__(self) -> None:
        self.consumed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.consumed = True
        raise AssertionError("collector response body was consumed")
        yield b""  # pragma: no cover - unreachable, makes this an async gen


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.outcomes: deque[int | httpx.TransportError] = deque()
        self.response_headers: dict[str, str] = {}
        self.body_stream: _ExplodingStream | None = None
        self.release = asyncio.Event()
        self.release.set()
        self.active_requests = 0
        self.max_active_requests = 0

    def set_outcomes(self, *outcomes: int | httpx.TransportError) -> None:
        self.outcomes.extend(outcomes)

    def block(self) -> None:
        self.release.clear()

    def unblock(self) -> None:
        self.release.set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        self.active_requests += 1
        self.max_active_requests = max(
            self.max_active_requests,
            self.active_requests,
        )
        try:
            await self.release.wait()
            outcome = self.outcomes.popleft() if self.outcomes else 200
            if isinstance(outcome, httpx.TransportError):
                raise outcome
            if self.body_stream is not None:
                return httpx.Response(
                    outcome,
                    headers=self.response_headers,
                    stream=self.body_stream,
                    request=request,
                )
            return httpx.Response(
                outcome,
                headers=self.response_headers,
                content=b"",
                request=request,
            )
        finally:
            self.active_requests -= 1


@pytest.fixture
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> _MockTransport:
    """Route every pool delivery through one deterministic transport."""
    transport = _MockTransport()
    monkeypatch.setattr(
        otel_relay,
        "_client_factory",
        lambda: httpx.AsyncClient(transport=transport),
    )
    return transport


@pytest.fixture
def receiver_identity(monkeypatch: pytest.MonkeyPatch) -> _ReceiverIdentity:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    return _ReceiverIdentity(
        workspace_id=workspace_id,
        organization_id=organization_id,
        session_id=session_id,
        token=mint_agent_otel_token(
            workspace_id=workspace_id,
            organization_id=organization_id,
            session_id=session_id,
        ),
    )


@pytest.fixture(autouse=True)
def isolated_delivery_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Keep tests fast: short timeout, no retry backoff.
    monkeypatch.setattr(otel_relay, "_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(otel_relay, "_RETRY_BASE_DELAY_SECONDS", 0.0)
    # The pool is module-global; a leaked task must not poison the next test.
    otel_relay._delivery_tasks.clear()
    otel_relay._post_semaphores.clear()
    otel_relay._pending_bytes = 0
    yield
    otel_relay._delivery_tasks.clear()
    otel_relay._post_semaphores.clear()
    otel_relay._pending_bytes = 0


@pytest.fixture
def short_socket_dir() -> Iterator[Path]:
    # macOS AF_UNIX path limit (~104 chars) — tmp_path is too long.
    with tempfile.TemporaryDirectory(prefix="otrelay-") as raw:
        yield Path(raw)


@pytest.fixture
async def started_receiver(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> AsyncIterator[OtelSocketReceiver]:
    receiver = OtelSocketReceiver(
        socket_path=short_socket_dir / "o.sock",
        collector_env={
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example.com/v1/traces",
        },
        headers={"Authorization": SecretStr("Bearer secret")},
        expected_workspace_id=receiver_identity.workspace_id,
        expected_organization_id=receiver_identity.organization_id,
        expected_session_id=receiver_identity.session_id,
    )
    await receiver.start()
    try:
        yield receiver
    finally:
        await receiver.stop()


async def _send_request(
    socket_path: Path,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    content_type: str = "application/x-protobuf",
    authorization: str | None = None,
    extra_headers: dict[str, str] | None = None,
    content_length: int | None = None,
) -> tuple[int, str, bytes]:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        head = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: receiver\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body) if content_length is None else content_length}\r\n"
            "Connection: close\r\n"
        ).encode("ascii")
        if authorization is not None:
            head += f"Authorization: {authorization}\r\n".encode("ascii")
        if extra_headers:
            for key, value in extra_headers.items():
                head += f"{key}: {value}\r\n".encode("ascii")
        head += b"\r\n"
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


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


async def _start_test_receiver(
    *,
    socket_path: Path,
    identity: _ReceiverIdentity,
) -> OtelSocketReceiver:
    receiver = OtelSocketReceiver(
        socket_path=socket_path,
        collector_env={"OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com"},
        headers={"Authorization": SecretStr("Bearer secret")},
        expected_workspace_id=identity.workspace_id,
        expected_organization_id=identity.organization_id,
        expected_session_id=identity.session_id,
    )
    await receiver.start()
    return receiver


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
async def test_receiver_forwards_post_with_injected_headers(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    body = b"\x0a\x05hello"
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/metrics",
        body=body,
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: len(mock_transport.requests) == 1)
    request = mock_transport.requests[0]
    # Generic endpoint + path (metrics has no signal-specific endpoint here)
    assert str(request.url) == "https://collector.example.com/v1/metrics"
    assert request.content == body
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["content-type"] == "application/x-protobuf"
    assert request.headers["user-agent"].startswith("tracecat-agent-otel-relay/")


@pytest.mark.anyio
async def test_receiver_uses_signal_specific_endpoint(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/traces",
        body=b"trace-bytes",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: len(mock_transport.requests) == 1)
    assert str(mock_transport.requests[0].url) == "https://traces.example.com/v1/traces"


@pytest.mark.anyio
async def test_receiver_uses_trusted_collector_authorization(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    await _wait_until(lambda: len(mock_transport.requests) == 1)
    request = mock_transport.requests[0]
    assert request.headers["authorization"] == "Bearer secret"


@pytest.mark.anyio
async def test_receiver_drops_inbound_sandbox_headers(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """Inbound headers other than the four the parser extracts must not be
    forwarded to the tenant collector. The delivery item carries an outbound
    header set built from scratch (user-agent + content-type echo + decrypted
    tenant headers); anything the sandbox tries to attach is dropped."""
    await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
        extra_headers={
            "X-Sandbox-Exfil": "secret-from-sandbox",
            "Cookie": "session=stolen",
            "X-Forwarded-For": "10.0.0.1",
        },
    )
    await _wait_until(lambda: len(mock_transport.requests) == 1)
    request = mock_transport.requests[0]
    # Only the three deliberate outbound headers are present.
    forwarded_keys = {key.lower() for key in request.headers.keys()}
    assert "x-sandbox-exfil" not in forwarded_keys
    assert "cookie" not in forwarded_keys
    assert "x-forwarded-for" not in forwarded_keys
    # The JWT envelope itself does not leak through either; the tenant
    # collector sees only the trusted-side Authorization header.
    assert request.headers["authorization"] == "Bearer secret"


@pytest.mark.anyio
async def test_receiver_rejects_missing_token(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
    )
    assert status == 401
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_receiver_rejects_invalid_token(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization="Bearer not-a-jwt",
    )
    assert status == 401
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_receiver_rejects_mismatched_claims(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    wrong_session_token = mint_agent_otel_token(
        workspace_id=receiver_identity.workspace_id,
        organization_id=receiver_identity.organization_id,
        session_id=uuid.uuid4(),
    )
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {wrong_session_token}",
    )
    assert status == 403
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_receiver_rejects_non_post_with_405(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="GET",
        path="/v1/metrics",
    )
    assert status == 405
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_receiver_rejects_unknown_path_with_404(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/health",
    )
    assert status == 404
    assert mock_transport.requests == []


@pytest.mark.anyio
async def test_receiver_returns_local_success_without_awaiting_collector(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "async.sock",
        identity=receiver_identity,
    )
    try:
        status, _, _ = await asyncio.wait_for(
            _send_request(
                receiver.socket_path,
                method="POST",
                path="/v1/logs",
                body=b"log",
                authorization=f"Bearer {receiver_identity.token}",
            ),
            timeout=0.5,
        )
        assert status == 202
        await _wait_until(lambda: mock_transport.active_requests == 1)
        assert len(otel_relay._delivery_tasks) == 1
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_receiver_rejects_invalid_content_type_before_admission(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        content_type="text/plain",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 415
    assert mock_transport.requests == []
    assert not otel_relay._delivery_tasks


@pytest.mark.anyio
async def test_receiver_canonicalizes_valid_content_type(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        content_type="Application/JSON; charset=utf-8",
        body=b"{}",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: len(mock_transport.requests) == 1)
    assert mock_transport.requests[0].headers["content-type"] == "application/json"


@pytest.mark.anyio
async def test_receiver_rejects_oversized_body_before_admission(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        authorization=f"Bearer {receiver_identity.token}",
        content_length=MAX_BODY_SIZE + 1,
    )
    assert status == 413
    assert mock_transport.requests == []
    assert not otel_relay._delivery_tasks


@pytest.mark.anyio
async def test_pool_bounds_pending_items_and_concurrent_requests(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(otel_relay, "_MAX_PENDING_ITEMS", 5)
    monkeypatch.setattr(otel_relay, "_MAX_CONCURRENT_REQUESTS", 2)
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "capacity.sock",
        identity=receiver_identity,
    )
    try:
        statuses = []
        for _ in range(8):
            status, _, _ = await _send_request(
                receiver.socket_path,
                method="POST",
                path="/v1/metrics",
                body=b"x",
                authorization=f"Bearer {receiver_identity.token}",
            )
            statuses.append(status)

        await _wait_until(lambda: mock_transport.active_requests == 2)
        assert statuses == [202, 202, 202, 202, 202, 503, 503, 503]
        assert len(otel_relay._delivery_tasks) == 5
        assert otel_relay._pending_bytes == 5
        assert mock_transport.max_active_requests == 2

        mock_transport.unblock()
        await _wait_until(lambda: not otel_relay._delivery_tasks)
        assert otel_relay._pending_bytes == 0
        assert mock_transport.max_active_requests == 2
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_pool_bounds_pending_bytes(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(otel_relay, "_MAX_PENDING_BYTES", 3)
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "bytes.sock",
        identity=receiver_identity,
    )
    try:
        first, _, _ = await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/logs",
            body=b"abc",
            authorization=f"Bearer {receiver_identity.token}",
        )
        second, _, _ = await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/logs",
            body=b"d",
            authorization=f"Bearer {receiver_identity.token}",
        )
        assert first == 202
        assert second == 503
        assert otel_relay._pending_bytes == 3
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_receiver_sheds_past_per_turn_cap_with_global_budget_free(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One turn cannot monopolize the pool even when the pool has room."""
    monkeypatch.setattr(otel_relay, "_MAX_PENDING_ITEMS_PER_TURN", 2)
    monkeypatch.setattr(otel_relay, "_MAX_PENDING_ITEMS", 100)
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "fair.sock",
        identity=receiver_identity,
    )
    try:
        statuses = []
        for _ in range(4):
            status, _, _ = await _send_request(
                receiver.socket_path,
                method="POST",
                path="/v1/metrics",
                body=b"x",
                authorization=f"Bearer {receiver_identity.token}",
            )
            statuses.append(status)

        assert statuses == [202, 202, 503, 503]
        # Shed by the per-turn cap, not the global one.
        assert len(otel_relay._delivery_tasks) == 2
        assert receiver._rejected["turn_capacity"] == 2
        assert receiver._rejected["pool_capacity"] == 0
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "first_outcome",
    [
        503,
        httpx.ConnectError("collector unavailable"),
    ],
    ids=["transient-status", "transport-error"],
)
async def test_pool_retries_transient_failures(
    first_outcome: int | httpx.TransportError,
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.set_outcomes(first_outcome, 200)
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/traces",
        body=b"trace",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 2
    assert otel_relay._pending_bytes == 0


@pytest.mark.anyio
async def test_pool_does_not_retry_terminal_status(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.set_outcomes(400, 200)
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 1
    assert otel_relay._pending_bytes == 0


@pytest.mark.anyio
async def test_pool_stops_after_retry_exhaustion(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.set_outcomes(503, 503, 503, 200)
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/metrics",
        body=b"metric",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 3
    assert otel_relay._pending_bytes == 0


@pytest.mark.anyio
async def test_stop_returns_promptly_and_delivery_still_completes(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """stop() closes ingress only; the pool owns admitted items."""
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "detach.sock",
        identity=receiver_identity,
    )
    status, _, _ = await _send_request(
        receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: mock_transport.active_requests == 1)

    # Returns while the delivery is still blocked in the transport.
    await asyncio.wait_for(receiver.stop(), timeout=0.5)
    assert len(otel_relay._delivery_tasks) == 1

    mock_transport.unblock()
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 1
    assert otel_relay._pending_bytes == 0


@pytest.mark.anyio
async def test_stop_closes_ingress_but_not_admitted_items(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "ingress.sock",
        identity=receiver_identity,
    )
    status, _, _ = await _send_request(
        receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: mock_transport.active_requests == 1)

    await receiver.stop()

    # New connections fail once the socket is gone.
    with pytest.raises((ConnectionRefusedError, FileNotFoundError, OSError)):
        await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/logs",
            body=b"log",
            authorization=f"Bearer {receiver_identity.token}",
        )

    # The already-admitted item is unaffected.
    mock_transport.unblock()
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 1
    assert otel_relay._pending_bytes == 0


def test_sweep_refunds_budget_for_tasks_stranded_on_closed_loops() -> None:
    """A task on a closed loop never runs its finally; the sweep refunds it."""
    delivery = otel_relay._OtelDelivery(
        collector_url="https://collector.example.com/v1/logs",
        content_type="application/x-protobuf",
        body=b"stranded-payload",
        headers={},
        signal_path="/v1/logs",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )

    loop = asyncio.new_event_loop()

    async def _admit_and_block() -> asyncio.Task[None] | None:
        # Never resolves, so the task is still pending when the loop closes.
        assert otel_relay._reserve_pending_bytes(len(delivery.body))
        return otel_relay._spawn_delivery(delivery, len(delivery.body))

    task = loop.run_until_complete(_admit_and_block())
    assert task is not None
    assert otel_relay._pending_bytes == len(delivery.body)
    assert task in otel_relay._delivery_tasks

    # Closed with the task still pending, so its done callback never fires.
    loop.close()
    assert not task.done()
    assert otel_relay._pending_bytes == len(delivery.body)

    otel_relay._sweep_closed_loops()

    assert otel_relay._pending_bytes == 0
    assert task not in otel_relay._delivery_tasks
    assert not otel_relay._post_semaphores


@pytest.mark.anyio
async def test_receiver_rejects_bad_token_without_reading_body(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
) -> None:
    """The 401 lands from headers alone; no body byte is ever buffered."""
    reader, writer = await asyncio.open_unix_connection(
        str(started_receiver.socket_path)
    )
    try:
        writer.write(
            b"POST /v1/logs HTTP/1.1\r\n"
            b"Host: receiver\r\n"
            b"Content-Type: application/x-protobuf\r\n"
            b"Content-Length: 8388608\r\n"
            b"Authorization: Bearer not-a-jwt\r\n"
            b"\r\n"
        )
        await writer.drain()
        # No body follows: the response must arrive without one.
        status_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert int(status_line.decode("ascii").split(" ")[1]) == 401
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    assert mock_transport.requests == []
    assert not otel_relay._delivery_tasks


@pytest.mark.anyio
async def test_receiver_rejects_oversized_content_length_headers_only(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """An over-limit declared length is a 413 before any body is read."""
    reader, writer = await asyncio.open_unix_connection(
        str(started_receiver.socket_path)
    )
    try:
        writer.write(
            b"POST /v1/logs HTTP/1.1\r\n"
            b"Host: receiver\r\n"
            b"Content-Type: application/x-protobuf\r\n"
            + f"Content-Length: {MAX_BODY_SIZE + 1}\r\n".encode("ascii")
            + f"Authorization: Bearer {receiver_identity.token}\r\n".encode("ascii")
            + b"\r\n"
        )
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert int(status_line.decode("ascii").split(" ")[1]) == 413
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    assert mock_transport.requests == []
    assert not otel_relay._delivery_tasks


@pytest.mark.anyio
async def test_receiver_requires_content_length(
    started_receiver: OtelSocketReceiver,
    receiver_identity: _ReceiverIdentity,
) -> None:
    reader, writer = await asyncio.open_unix_connection(
        str(started_receiver.socket_path)
    )
    try:
        writer.write(
            b"POST /v1/logs HTTP/1.1\r\n"
            b"Host: receiver\r\n"
            b"Content-Type: application/x-protobuf\r\n"
            + f"Authorization: Bearer {receiver_identity.token}\r\n".encode("ascii")
            + b"\r\n"
        )
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert int(status_line.decode("ascii").split(" ")[1]) == 411
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.mark.anyio
async def test_receiver_rejects_oversized_header_section(
    started_receiver: OtelSocketReceiver,
) -> None:
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        extra_headers={"X-Padding": "a" * (otel_relay._MAX_HEADER_SECTION_SIZE + 1)},
    )
    assert status == 431


@pytest.mark.anyio
async def test_receiver_refuses_connections_over_the_cap(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connections past the cap are shed; the held ones still work."""
    monkeypatch.setattr(otel_relay, "_MAX_CONNECTIONS_PER_TURN", 2)
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "conncap.sock",
        identity=receiver_identity,
    )
    held: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
    try:
        # Open (but do not complete) two requests so both handlers stay alive.
        for _ in range(2):
            reader, writer = await asyncio.open_unix_connection(
                str(receiver.socket_path)
            )
            writer.write(b"POST /v1/logs HTTP/1.1\r\n")
            await writer.drain()
            held.append((reader, writer))
        await _wait_until(lambda: len(receiver._connection_tasks) == 2)

        over_reader, over_writer = await asyncio.open_unix_connection(
            str(receiver.socket_path)
        )
        try:
            status_line = await asyncio.wait_for(over_reader.readline(), timeout=1.0)
            assert int(status_line.decode("ascii").split(" ")[1]) == 503
        finally:
            over_writer.close()
            try:
                await over_writer.wait_closed()
            except Exception:
                pass
        assert receiver._rejected["connections"] == 1

        # Finishing a held request frees a slot, and a fresh one is admitted.
        reader, writer = held.pop()
        writer.write(
            b"Host: receiver\r\n"
            b"Content-Type: application/x-protobuf\r\n"
            b"Content-Length: 3\r\n"
            + f"Authorization: Bearer {receiver_identity.token}\r\n".encode("ascii")
            + b"\r\nlog"
        )
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert int(status_line.decode("ascii").split(" ")[1]) == 202
        writer.close()
        await _wait_until(lambda: len(mock_transport.requests) == 1)
    finally:
        for _, writer in held:
            writer.close()
        await receiver.stop()


@pytest.mark.anyio
async def test_receiver_reserves_budget_before_reading_body(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An over-budget declared length is shed before a body byte is read."""
    monkeypatch.setattr(otel_relay, "_MAX_PENDING_BYTES", 8)
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "reserve.sock",
        identity=receiver_identity,
    )
    try:
        first, _, _ = await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/logs",
            body=b"abcdefgh",
            authorization=f"Bearer {receiver_identity.token}",
        )
        assert first == 202
        assert otel_relay._pending_bytes == 8

        # Head only: the 503 must land while the body is still unsent.
        reader, writer = await asyncio.open_unix_connection(str(receiver.socket_path))
        try:
            writer.write(
                b"POST /v1/logs HTTP/1.1\r\n"
                b"Host: receiver\r\n"
                b"Content-Type: application/x-protobuf\r\n"
                b"Content-Length: 4\r\n"
                + f"Authorization: Bearer {receiver_identity.token}\r\n".encode("ascii")
                + b"\r\n"
            )
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
            assert int(status_line.decode("ascii").split(" ")[1]) == 503
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        # Only the first request's reservation is still held.
        assert otel_relay._pending_bytes == 8
        assert len(otel_relay._delivery_tasks) == 1
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_receiver_refunds_reservation_on_client_disconnect(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """A body that never arrives in full must not strand its reservation."""
    _, writer = await asyncio.open_unix_connection(str(started_receiver.socket_path))
    writer.write(
        b"POST /v1/logs HTTP/1.1\r\n"
        b"Host: receiver\r\n"
        b"Content-Type: application/x-protobuf\r\n"
        b"Content-Length: 64\r\n"
        + f"Authorization: Bearer {receiver_identity.token}\r\n".encode("ascii")
        + b"\r\npartial"
    )
    await writer.drain()
    await _wait_until(lambda: otel_relay._pending_bytes == 64)

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    await _wait_until(lambda: otel_relay._pending_bytes == 0)
    assert mock_transport.requests == []
    assert not otel_relay._delivery_tasks


@pytest.mark.anyio
async def test_receiver_times_out_body_read_and_refunds(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled body read returns 408 and releases its reservation."""
    monkeypatch.setattr(otel_relay, "_BODY_READ_TIMEOUT_SECONDS", 0.05)
    reader, writer = await asyncio.open_unix_connection(
        str(started_receiver.socket_path)
    )
    try:
        writer.write(
            b"POST /v1/logs HTTP/1.1\r\n"
            b"Host: receiver\r\n"
            b"Content-Type: application/x-protobuf\r\n"
            b"Content-Length: 32\r\n"
            + f"Authorization: Bearer {receiver_identity.token}\r\n".encode("ascii")
            + b"\r\n"
        )
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        assert int(status_line.decode("ascii").split(" ")[1]) == 408
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    await _wait_until(lambda: otel_relay._pending_bytes == 0)
    assert started_receiver._rejected["timeout"] == 1
    assert mock_transport.requests == []
    assert not otel_relay._delivery_tasks


@pytest.mark.anyio
async def test_pool_never_consumes_collector_response_body(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """A hostile collector's response body is never buffered by the host."""
    stream = _ExplodingStream()
    mock_transport.body_stream = stream
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    # Delivery succeeded on the status line alone; one attempt, body untouched.
    assert len(mock_transport.requests) == 1
    assert stream.consumed is False
    assert otel_relay._pending_bytes == 0


@pytest.mark.anyio
async def test_pool_does_not_retry_internal_server_error(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """500 is terminal per the OTLP spec: retrying could duplicate a batch."""
    mock_transport.set_outcomes(500, 200)
    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 1
    assert otel_relay._pending_bytes == 0


@pytest.mark.anyio
async def test_pool_honors_retry_after_header(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429's Retry-After replaces backoff, capped by the module limit."""
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _record_sleep(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(otel_relay.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(otel_relay, "_MAX_RETRY_AFTER_SECONDS", 2.0)
    mock_transport.response_headers = {"Retry-After": "9"}
    mock_transport.set_outcomes(429, 200)

    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 2
    # Capped at _MAX_RETRY_AFTER_SECONDS rather than the header's 9s.
    assert slept == [2.0]


@pytest.mark.anyio
async def test_pool_falls_back_to_backoff_for_malformed_retry_after(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An http-date Retry-After is ignored; normal jittered backoff applies."""
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _record_sleep(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(otel_relay.asyncio, "sleep", _record_sleep)
    mock_transport.response_headers = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    mock_transport.set_outcomes(503, 200)

    status, _, _ = await _send_request(
        started_receiver.socket_path,
        method="POST",
        path="/v1/logs",
        body=b"log",
        authorization=f"Bearer {receiver_identity.token}",
    )
    assert status == 202
    await _wait_until(lambda: not otel_relay._delivery_tasks)
    assert len(mock_transport.requests) == 2
    # Base delay is monkeypatched to 0, so jitter multiplies out to zero.
    assert slept == [0.0]


def test_parse_retry_after_accepts_only_capped_integer_seconds() -> None:
    assert otel_relay._parse_retry_after(429, "5") == 5.0
    assert (
        otel_relay._parse_retry_after(503, "600") == otel_relay._MAX_RETRY_AFTER_SECONDS
    )
    # Only 429/503 carry a spec-honored Retry-After.
    assert otel_relay._parse_retry_after(500, "5") is None
    assert otel_relay._parse_retry_after(429, "not-a-number") is None
    assert otel_relay._parse_retry_after(503, "-1") is None
    assert otel_relay._parse_retry_after(503, None) is None


@pytest.mark.anyio
async def test_retry_backoff_is_jittered(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two retries at the same attempt number must not share a fixed delay."""
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _record_sleep(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(otel_relay.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(otel_relay, "_RETRY_BASE_DELAY_SECONDS", 1.0)

    for _ in range(2):
        mock_transport.set_outcomes(503, 200)
        status, _, _ = await _send_request(
            started_receiver.socket_path,
            method="POST",
            path="/v1/logs",
            body=b"log",
            authorization=f"Bearer {receiver_identity.token}",
        )
        assert status == 202
        await _wait_until(lambda: not otel_relay._delivery_tasks)

    assert len(slept) == 2
    # Jittered into [base, 1.5 * base); a deterministic backoff would be exact.
    assert all(1.0 <= delay < 1.5 for delay in slept)
    assert slept[0] != slept[1]


@pytest.mark.anyio
async def test_resolve_disabled_telemetry_returns_empty_envs() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(enabled=False),
        org_headers=None,
    )
    assert resolved.enabled is False
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}


def test_sandbox_env_carries_no_endpoint() -> None:
    config_value = AgentOtelConfig(
        enabled=True,
        endpoint=HttpUrl("https://generic.example.com"),
        traces_enabled=True,
        log_user_prompts=True,
        resource_attributes={"service.name": "tracecat"},
    )
    resolved = resolve_agent_otel_config(
        org_config=config_value,
        org_headers={"Authorization": "Bearer t"},
    )

    sandbox_env = resolved.sandbox_env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in sandbox_env
    assert sandbox_env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert sandbox_env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert sandbox_env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert sandbox_env["OTEL_LOG_USER_PROMPTS"] == "1"
    assert sandbox_env["OTEL_RESOURCE_ATTRIBUTES"] == "service.name=tracecat"
    # Tenant headers never reach the resolver's sandbox env (the activity later
    # injects only a receiver-bearer JWT as OTEL_EXPORTER_OTLP_HEADERS).
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in sandbox_env
    # Tenant headers and collector endpoint remain trusted-side only.
    assert resolved.collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "https://generic.example.com/"
    )
    assert resolved.headers["Authorization"].get_secret_value() == "Bearer t"


def test_resolved_config_default_is_disabled() -> None:
    resolved = ResolvedAgentOtelConfig()
    assert resolved.enabled is False
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}
