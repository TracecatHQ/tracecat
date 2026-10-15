from __future__ import annotations

import asyncio
import stat
import tempfile
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from pydantic import HttpUrl, SecretStr

from tracecat import config
from tracecat.agent.otel_config import (
    AgentOtelConfig,
    ResolvedAgentOtelConfig,
    resolve_agent_otel_config,
)
from tracecat.agent.otel_delivery import (
    DeliveryLimits,
    OtelDeliveryPool,
    collector_client,
)
from tracecat.agent.sandbox import otel_relay
from tracecat.agent.sandbox.otel_relay import (
    MAX_BODY_SIZE,
    OtelRoutingPlan,
    OtelSocketReceiver,
    PlatformTraceParent,
    canonicalize_tenant_trace_body,
    project_platform_trace_body,
    resolve_collector_url,
    tenant_signal_enabled,
)
from tracecat.agent.tokens import mint_agent_otel_token


@dataclass(frozen=True, slots=True)
class _ReceiverIdentity:
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    token: str


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.release = asyncio.Event()
        self.release.set()
        self.active_requests = 0
        self.max_active_requests = 0

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
            return httpx.Response(200, content=b"", request=request)
        finally:
            self.active_requests -= 1


type _PoolFactory = Callable[[DeliveryLimits | None], OtelDeliveryPool]


@pytest.fixture
def mock_transport() -> _MockTransport:
    """One deterministic transport for every delivery in a test."""
    return _MockTransport()


@pytest.fixture
async def make_pool(mock_transport: _MockTransport) -> AsyncIterator[_PoolFactory]:
    """Build delivery pools over the mock transport; drain them at teardown."""
    pools: list[OtelDeliveryPool] = []
    async with collector_client(trust_env=False, transport=mock_transport) as client:

        def make(limits: DeliveryLimits | None) -> OtelDeliveryPool:
            pool = OtelDeliveryPool(client, limits)
            pools.append(pool)
            return pool

        yield make
        mock_transport.unblock()
        for pool in pools:
            await pool.drain()


@pytest.fixture
def delivery_pool(make_pool: _PoolFactory) -> OtelDeliveryPool:
    return make_pool(None)


def _mint_identity(organization_id: uuid.UUID | None = None) -> _ReceiverIdentity:
    workspace_id = uuid.uuid4()
    organization_id = organization_id or uuid.uuid4()
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


@pytest.fixture
def receiver_identity(monkeypatch: pytest.MonkeyPatch) -> _ReceiverIdentity:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    return _mint_identity()


@pytest.fixture
def short_socket_dir() -> Iterator[Path]:
    # macOS AF_UNIX path limit (~104 chars) — tmp_path is too long.
    with tempfile.TemporaryDirectory(prefix="otrelay-") as raw:
        yield Path(raw)


@pytest.fixture
async def started_receiver(
    short_socket_dir: Path,
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
) -> AsyncIterator[OtelSocketReceiver]:
    receiver = OtelSocketReceiver(
        socket_path=short_socket_dir / "o.sock",
        plan=OtelRoutingPlan.build(
            collector_env={
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces.example.com/v1/traces",
            },
            headers={"Authorization": SecretStr("Bearer secret")},
        ),
        pool=delivery_pool,
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
    pool: OtelDeliveryPool,
) -> OtelSocketReceiver:
    receiver = OtelSocketReceiver(
        socket_path=socket_path,
        plan=OtelRoutingPlan.build(
            collector_env={
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com"
            },
            headers={"Authorization": SecretStr("Bearer secret")},
        ),
        pool=pool,
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


def test_tenant_signal_enabled_reads_the_per_signal_exporter() -> None:
    assert (
        tenant_signal_enabled({"OTEL_TRACES_EXPORTER": "none"}, "/v1/traces") is False
    )
    assert tenant_signal_enabled({"OTEL_TRACES_EXPORTER": "otlp"}, "/v1/traces") is True
    assert tenant_signal_enabled({}, "/v1/traces") is True


def test_platform_native_trace_is_sanitized_and_joined_under_trusted_parent() -> None:
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    resource_spans.resource.attributes.append(
        KeyValue(
            key="tracecat.workflow.execution.id",
            value=AnyValue(string_value="sandbox-spoofed"),
        )
    )
    scope_spans = resource_spans.scope_spans.add()
    root = scope_spans.spans.add(
        name="claude_code.interaction",
        trace_id=b"\x11" * 16,
        span_id=b"\x21" * 8,
        flags=0,
    )
    root.attributes.extend(
        [
            KeyValue(key="user_prompt_length", value=AnyValue(int_value=42)),
            KeyValue(
                key="user_prompt", value=AnyValue(string_value="sensitive prompt")
            ),
        ]
    )
    root.events.add(
        name="prompt",
        attributes=[
            KeyValue(key="content", value=AnyValue(string_value="sensitive event"))
        ],
    )
    tool = scope_spans.spans.add(
        name="claude_code.tool",
        trace_id=b"\x11" * 16,
        span_id=b"\x22" * 8,
        parent_span_id=root.span_id,
        flags=0,
    )
    tool.attributes.extend(
        [
            KeyValue(key="tool_name", value=AnyValue(string_value="Read")),
            KeyValue(
                key="file_path", value=AnyValue(string_value="/private/customer.txt")
            ),
        ]
    )
    parent = PlatformTraceParent(
        trace_id=b"\xaa" * 16,
        span_id=b"\xbb" * 8,
        trace_flags=1,
        resource_attributes={
            "tracecat.workflow.execution.id": "wf_synthetic/exec_synthetic",
            "tracecat.agent.run.id": "00000000-0000-4000-8000-000000000001",
        },
    )

    rewritten = project_platform_trace_body(
        request.SerializeToString(),
        content_type="application/x-protobuf",
        parent=parent,
    )

    assert rewritten is not None
    parsed = ExportTraceServiceRequest.FromString(rewritten)
    parsed_resource_spans = parsed.resource_spans[0]
    parsed_spans = parsed_resource_spans.scope_spans[0].spans
    assert {span.trace_id for span in parsed_spans} == {parent.trace_id}
    assert parsed_spans[0].parent_span_id == parent.span_id
    assert parsed_spans[1].parent_span_id == parsed_spans[0].span_id
    assert all(span.flags & 1 for span in parsed_spans)
    assert [attribute.key for attribute in parsed_spans[0].attributes] == [
        "user_prompt_length"
    ]
    assert [attribute.key for attribute in parsed_spans[1].attributes] == ["tool_name"]
    assert not parsed_spans[0].events
    assert not parsed_spans[1].events
    attributes = {
        attribute.key: attribute.value.string_value
        for attribute in parsed_resource_spans.resource.attributes
    }
    assert attributes == {
        "service.name": "tracecat-agent-native",
        "service.namespace": "tracecat",
        **parent.resource_attributes,
    }


def test_tenant_trace_keeps_topology_and_deduplicates_trusted_attributes() -> None:
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    resource_spans.resource.attributes.extend(
        [
            KeyValue(key="tracecat.agent.run.id", value=AnyValue(string_value="one")),
            KeyValue(key="tracecat.agent.run.id", value=AnyValue(string_value="two")),
        ]
    )
    span = resource_spans.scope_spans.add().spans.add(
        name="claude_code.interaction",
        trace_id=b"\x11" * 16,
        span_id=b"\x21" * 8,
    )

    rewritten = canonicalize_tenant_trace_body(
        request.SerializeToString(),
        content_type="application/x-protobuf",
        trusted_attributes={"tracecat.agent.run.id": "trusted"},
    )

    parsed = ExportTraceServiceRequest.FromString(rewritten)
    parsed_resource = parsed.resource_spans[0]
    assert parsed_resource.scope_spans[0].spans[0].trace_id == span.trace_id
    run_attributes = [
        attribute.value.string_value
        for attribute in parsed_resource.resource.attributes
        if attribute.key == "tracecat.agent.run.id"
    ]
    assert run_attributes == ["trusted"]


def test_non_platform_trace_body_is_not_projected() -> None:
    body = b"synthetic-tenant-payload"
    parent = PlatformTraceParent(
        trace_id=b"\xaa" * 16,
        span_id=b"\xbb" * 8,
        trace_flags=1,
        resource_attributes={},
    )

    assert (
        project_platform_trace_body(
            body,
            content_type="application/x-protobuf",
            parent=parent,
        )
        is None
    )


@pytest.mark.anyio
async def test_trace_batch_fans_out_original_and_sanitized_copies(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tenant copy is charged to the org's share and the redacted copy to the
    platform's, with the protobuf rewrite done off the event loop."""
    projection_threads: list[int] = []
    real_prepare = otel_relay._prepare_trace_bodies

    def track_prepare(
        body: bytes,
        *,
        content_type: str,
        parent: PlatformTraceParent | None,
    ) -> otel_relay._PreparedTraceBodies:
        projection_threads.append(threading.get_ident())
        return real_prepare(body, content_type=content_type, parent=parent)

    monkeypatch.setattr(otel_relay, "_prepare_trace_bodies", track_prepare)
    mock_transport.block()
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    span = resource_spans.scope_spans.add().spans.add(
        name="claude_code.llm_request",
        trace_id=b"\x11" * 16,
        span_id=b"\x21" * 8,
    )
    span.attributes.extend(
        [
            KeyValue(key="input_tokens", value=AnyValue(int_value=12)),
            KeyValue(
                key="request.prompt", value=AnyValue(string_value="tenant content")
            ),
        ]
    )
    parent = PlatformTraceParent(
        trace_id=b"\xaa" * 16,
        span_id=b"\xbb" * 8,
        trace_flags=1,
        resource_attributes={"tracecat.agent.run.id": str(uuid.uuid4())},
    )
    receiver = OtelSocketReceiver(
        socket_path=short_socket_dir / "fanout.sock",
        plan=OtelRoutingPlan.build(
            collector_env={"OTEL_EXPORTER_OTLP_ENDPOINT": "https://tenant.example.com"},
            headers={"Authorization": SecretStr("Bearer tenant")},
            platform_trace_parent=parent,
            platform_collector_env={
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-gateway.internal:4318"
            },
        ),
        pool=delivery_pool,
        expected_workspace_id=receiver_identity.workspace_id,
        expected_organization_id=receiver_identity.organization_id,
        expected_session_id=receiver_identity.session_id,
    )
    await receiver.start()
    try:
        status, _, _ = await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/traces",
            body=request.SerializeToString(),
            authorization=f"Bearer {receiver_identity.token}",
        )
        assert status == 202
        await _wait_until(lambda: mock_transport.active_requests == 2)
        shares = delivery_pool._shares
        assert shares.keys() == {receiver_identity.organization_id, "platform"}
        assert shares[receiver_identity.organization_id].items == 1
        assert shares["platform"].items == 1
        mock_transport.unblock()
        await _wait_until(lambda: not delivery_pool._tasks)
    finally:
        mock_transport.unblock()
        await receiver.stop()

    assert len(mock_transport.requests) == 2
    assert len(projection_threads) == 1
    assert projection_threads[0] != threading.get_ident()
    requests_by_host = {
        request.url.host: request for request in mock_transport.requests
    }
    tenant_request = ExportTraceServiceRequest.FromString(
        requests_by_host["tenant.example.com"].content
    )
    platform_request = ExportTraceServiceRequest.FromString(
        requests_by_host["otel-gateway.internal"].content
    )
    tenant_span = tenant_request.resource_spans[0].scope_spans[0].spans[0]
    platform_span = platform_request.resource_spans[0].scope_spans[0].spans[0]
    assert tenant_span.trace_id == b"\x11" * 16
    assert {attribute.key for attribute in tenant_span.attributes} == {
        "input_tokens",
        "request.prompt",
    }
    assert requests_by_host["tenant.example.com"].headers["authorization"] == (
        "Bearer tenant"
    )
    assert platform_span.trace_id == parent.trace_id
    assert {attribute.key for attribute in platform_span.attributes} == {"input_tokens"}
    assert "authorization" not in requests_by_host["otel-gateway.internal"].headers


@pytest.mark.anyio
async def test_tenant_trace_opt_out_skips_tenant_delivery_but_keeps_platform(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
) -> None:
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    span = resource_spans.scope_spans.add().spans.add(
        name="claude_code.llm_request",
        trace_id=b"\x11" * 16,
        span_id=b"\x21" * 8,
    )
    span.attributes.append(KeyValue(key="input_tokens", value=AnyValue(int_value=12)))
    parent = PlatformTraceParent(
        trace_id=b"\xaa" * 16,
        span_id=b"\xbb" * 8,
        trace_flags=1,
        resource_attributes={"tracecat.agent.run.id": str(uuid.uuid4())},
    )
    receiver = OtelSocketReceiver(
        socket_path=short_socket_dir / "optout.sock",
        plan=OtelRoutingPlan.build(
            collector_env={
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://tenant.example.com",
                "OTEL_TRACES_EXPORTER": "none",
            },
            headers={"Authorization": SecretStr("Bearer tenant")},
            platform_trace_parent=parent,
            platform_collector_env={
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-gateway.internal:4318"
            },
        ),
        pool=delivery_pool,
        expected_workspace_id=receiver_identity.workspace_id,
        expected_organization_id=receiver_identity.organization_id,
        expected_session_id=receiver_identity.session_id,
    )
    await receiver.start()
    try:
        status, _, _ = await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/traces",
            body=request.SerializeToString(),
            authorization=f"Bearer {receiver_identity.token}",
        )
        assert status == 202
        await _wait_until(lambda: not delivery_pool._tasks)
    finally:
        await receiver.stop()

    # The org's reservation for the body is refunded once the copy is admitted.
    assert delivery_pool._shares == {}
    assert len(mock_transport.requests) == 1
    assert mock_transport.requests[0].url.host == "otel-gateway.internal"
    assert not [
        delivered
        for delivered in mock_transport.requests
        if delivered.url.host == "tenant.example.com"
    ]


@pytest.mark.anyio
async def test_signal_without_destination_returns_no_collector_and_refunds(
    short_socket_dir: Path,
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """A signal opted out of every route is rejected without consuming budget."""
    metrics = ExportMetricsServiceRequest()
    metrics.resource_metrics.add().scope_metrics.add().metrics.add(
        name="synthetic.metric"
    )
    body = metrics.SerializeToString()
    receiver = OtelSocketReceiver(
        socket_path=short_socket_dir / "no-collector.sock",
        plan=OtelRoutingPlan.build(
            collector_env={
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com",
                "OTEL_METRICS_EXPORTER": "none",
            },
            headers={},
        ),
        pool=delivery_pool,
        expected_workspace_id=receiver_identity.workspace_id,
        expected_organization_id=receiver_identity.organization_id,
        expected_session_id=receiver_identity.session_id,
    )
    await receiver.start()
    try:
        status, reason, _ = await _send_request(
            receiver.socket_path,
            method="POST",
            path="/v1/metrics",
            body=body,
            authorization=f"Bearer {receiver_identity.token}",
        )
    finally:
        await receiver.stop()

    assert status == 503
    assert reason == "No Collector"
    assert receiver._rejected["collector"] == 1
    assert not delivery_pool._tasks
    assert delivery_pool._reserved == 0
    assert delivery_pool._shares == {}


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
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "async.sock",
        identity=receiver_identity,
        pool=delivery_pool,
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
        assert len(delivery_pool._tasks) == 1
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_receiver_rejects_invalid_content_type_before_admission(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
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
    assert not delivery_pool._tasks


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
    delivery_pool: OtelDeliveryPool,
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
    assert not delivery_pool._tasks


@pytest.mark.anyio
async def test_pool_bounds_pending_items_and_concurrent_requests(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    make_pool: _PoolFactory,
    receiver_identity: _ReceiverIdentity,
) -> None:
    pool = make_pool(DeliveryLimits(max_items=5, posts=2))
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "capacity.sock",
        identity=receiver_identity,
        pool=pool,
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
        assert len(pool._tasks) == 5
        assert pool._reserved == 5
        assert mock_transport.max_active_requests == 2

        mock_transport.unblock()
        await _wait_until(lambda: not pool._tasks)
        assert pool._reserved == 0
        assert mock_transport.max_active_requests == 2
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_pool_bounds_pending_bytes(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    make_pool: _PoolFactory,
    receiver_identity: _ReceiverIdentity,
) -> None:
    pool = make_pool(DeliveryLimits(max_bytes=3))
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "bytes.sock",
        identity=receiver_identity,
        pool=pool,
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
        assert pool._reserved == 3
    finally:
        mock_transport.unblock()
        await receiver.stop()


async def _post_metric(
    receiver: OtelSocketReceiver, identity: _ReceiverIdentity
) -> int:
    status, _, _ = await _send_request(
        receiver.socket_path,
        method="POST",
        path="/v1/metrics",
        body=b"x",
        authorization=f"Bearer {identity.token}",
    )
    return status


@pytest.mark.anyio
async def test_receiver_sheds_past_the_org_share_with_pool_budget_free(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    make_pool: _PoolFactory,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """One org cannot monopolize the pool even when the pool has room."""
    pool = make_pool(DeliveryLimits(share_items=2, max_items=100))
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "fair.sock",
        identity=receiver_identity,
        pool=pool,
    )
    try:
        statuses = [await _post_metric(receiver, receiver_identity) for _ in range(4)]

        assert statuses == [202, 202, 503, 503]
        assert len(pool._tasks) == 2
        assert receiver._rejected["pool_capacity"] == 2
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_org_share_outlives_its_turn_and_spares_other_orgs(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    make_pool: _PoolFactory,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """Deliveries a finished turn left pending still count against its org, so the
    org's next turn is shed while another org's turn is admitted."""
    pool = make_pool(DeliveryLimits(share_items=2))
    mock_transport.block()
    first_turn = await _start_test_receiver(
        socket_path=short_socket_dir / "first.sock",
        identity=receiver_identity,
        pool=pool,
    )
    try:
        assert [
            await _post_metric(first_turn, receiver_identity) for _ in range(2)
        ] == [
            202,
            202,
        ]
    finally:
        await first_turn.stop()

    next_identity = _mint_identity(receiver_identity.organization_id)
    other_identity = _mint_identity()
    next_turn = await _start_test_receiver(
        socket_path=short_socket_dir / "next.sock",
        identity=next_identity,
        pool=pool,
    )
    other_org = await _start_test_receiver(
        socket_path=short_socket_dir / "other.sock",
        identity=other_identity,
        pool=pool,
    )
    try:
        assert await _post_metric(next_turn, next_identity) == 503
        assert await _post_metric(other_org, other_identity) == 202
        assert pool._shares[receiver_identity.organization_id].items == 2
        assert pool._shares[other_identity.organization_id].items == 1
    finally:
        mock_transport.unblock()
        await next_turn.stop()
        await other_org.stop()


@pytest.mark.anyio
async def test_stop_returns_promptly_and_delivery_still_completes(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """stop() closes ingress only; the pool owns admitted items."""
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "detach.sock",
        identity=receiver_identity,
        pool=delivery_pool,
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
    assert len(delivery_pool._tasks) == 1

    mock_transport.unblock()
    await _wait_until(lambda: not delivery_pool._tasks)
    assert len(mock_transport.requests) == 1
    assert delivery_pool._reserved == 0


@pytest.mark.anyio
async def test_stop_closes_ingress_but_not_admitted_items(
    short_socket_dir: Path,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
) -> None:
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "ingress.sock",
        identity=receiver_identity,
        pool=delivery_pool,
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
    await _wait_until(lambda: not delivery_pool._tasks)
    assert len(mock_transport.requests) == 1
    assert delivery_pool._reserved == 0


@pytest.mark.anyio
async def test_receiver_socket_is_private_replaces_stale_file_and_unlinks_on_stop(
    started_receiver: OtelSocketReceiver,
) -> None:
    """The relay owns a private socket and removes it cleanly on shutdown."""
    socket_path = started_receiver.socket_path
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

    await started_receiver.stop()
    assert not socket_path.exists()

    socket_path.write_text("stale socket placeholder")
    await started_receiver.start()
    assert stat.S_ISSOCK(socket_path.stat().st_mode)


@pytest.mark.anyio
async def test_receiver_rejects_bad_token_without_reading_body(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
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
    assert not delivery_pool._tasks


@pytest.mark.anyio
async def test_receiver_rejects_oversized_content_length_headers_only(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
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
    assert not delivery_pool._tasks


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
    delivery_pool: OtelDeliveryPool,
    receiver_identity: _ReceiverIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connections past the cap are shed; the held ones still work."""
    monkeypatch.setattr(otel_relay, "_MAX_CONNECTIONS_PER_TURN", 2)
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "conncap.sock",
        identity=receiver_identity,
        pool=delivery_pool,
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
    make_pool: _PoolFactory,
    receiver_identity: _ReceiverIdentity,
) -> None:
    """An over-budget declared length is shed before a body byte is read."""
    pool = make_pool(DeliveryLimits(max_bytes=8))
    mock_transport.block()
    receiver = await _start_test_receiver(
        socket_path=short_socket_dir / "reserve.sock",
        identity=receiver_identity,
        pool=pool,
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
        assert pool._reserved == 8

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
        assert pool._reserved == 8
        assert len(pool._tasks) == 1
    finally:
        mock_transport.unblock()
        await receiver.stop()


@pytest.mark.anyio
async def test_receiver_refunds_reservation_on_client_disconnect(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
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
    await _wait_until(lambda: delivery_pool._reserved == 64)

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    await _wait_until(lambda: delivery_pool._reserved == 0)
    assert delivery_pool._shares == {}
    assert mock_transport.requests == []
    assert not delivery_pool._tasks


@pytest.mark.anyio
async def test_receiver_times_out_body_read_and_refunds(
    started_receiver: OtelSocketReceiver,
    mock_transport: _MockTransport,
    delivery_pool: OtelDeliveryPool,
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

    await _wait_until(lambda: delivery_pool._reserved == 0)
    assert started_receiver._rejected["timeout"] == 1
    assert mock_transport.requests == []
    assert not delivery_pool._tasks


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
