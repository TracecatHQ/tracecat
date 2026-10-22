from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import Mock

import httpx
import orjson
import pytest
from tenacity import AsyncRetrying, RetryCallState

from tracecat.agent.diagnostics import MAX_LLM_ERROR_BODY_BYTES, LLMErrorDiagnostics
from tracecat.agent.observability import LLMGatewayLoadTracker
from tracecat.agent.sandbox import llm_proxy
from tracecat.agent.sandbox.llm_proxy import (
    LLMProxyError,
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
    ParsedRequest,
    _http_error_classification,
    _parse_retry_after,
    _rate_limit_wait,
    _rewrite_json_body,
    _UpstreamRateLimited,
)
from tracecat.agent.sandbox.tool_use_rewrite import ToolUseStreamRewriter
from tracecat.agent.tokens import LLMRouteClaim, mint_llm_token
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)


@pytest.fixture(autouse=True)
def no_rate_limit_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_proxy, "_RATE_LIMIT_BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(llm_proxy, "_RATE_LIMIT_MAX_DELAY_SECONDS", 0.0)


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        return None


class _FailingResponseStream(httpx.AsyncByteStream):
    def __init__(self, request: httpx.Request) -> None:
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx.ReadError("provider disconnected", request=self._request)
        yield b""  # pragma: no cover


def _routing_plan(
    *,
    managed_url: str = "http://litellm:4000",
    managed_provider: str = "openai",
    managed_local_provider_cleanup: bool = True,
    direct_routes: dict[str, LLMRoute] | None = None,
    direct_authorizations: dict[str, str] | None = None,
) -> LLMRoutingPlan:
    routing_plan = LLMRoutingPlan(
        managed_route=LLMRoute(
            base_url=managed_url,
            model_provider=managed_provider,
            mode="managed",
            local_provider_cleanup=managed_local_provider_cleanup,
        ),
        direct_routes=direct_routes or {},
    )
    return LLMRoutingPlan(
        managed_route=routing_plan.managed_route,
        direct_routes={
            route_key: replace(
                route, authorization=(direct_authorizations or {}).get(route_key)
            )
            for route_key, route in routing_plan.direct_routes.items()
        },
    )


def _direct_plan(
    base_url: str = "https://customer-provider.example",
    *,
    alias: str = "customer-alias",
    model_provider: str = "custom-model-provider",
    authorization: str | None = None,
    upstream_model_name: str | None = None,
) -> LLMRoutingPlan:
    return _routing_plan(
        direct_routes={
            alias: LLMRoute(
                base_url=base_url,
                model_provider=model_provider,
                upstream_model_name=upstream_model_name,
            )
        },
        direct_authorizations={alias: authorization} if authorization else None,
    )


_LLM_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer llm-token",
}
_HELLO = [{"role": "user", "content": "hello"}]


def _request(
    body: bytes | dict[str, object] = b'{"messages":[]}',
    *,
    method: str = "POST",
    path: str = "/v1/messages",
    headers: dict[str, str] | None = None,
) -> ParsedRequest:
    return cast(
        ParsedRequest,
        {
            "method": method,
            "path": path,
            "headers": {"Content-Type": "application/json"}
            if headers is None
            else headers,
            "body": body if isinstance(body, bytes) else orjson.dumps(body),
        },
    )


_RATE_LIMIT_REQUEST = _request(
    {"messages": _HELLO},
    headers={**_LLM_HEADERS, "X-Request-ID": "trace-rate-limit"},
)

type _Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


async def _forward(
    tmp_path: Path,
    handler: _Handler,
    request: ParsedRequest = _RATE_LIMIT_REQUEST,
    *,
    routing_plan: LLMRoutingPlan | None = None,
) -> tuple[str, list[LLMProxyError]]:
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=routing_plan or _routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        socket_proxy._client = client
        socket_proxy._direct_client = client
        await socket_proxy._forward_request(request, cast(asyncio.StreamWriter, writer))
    return writer.buffer.decode("utf-8"), errors


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, headers={"Content-Type": "application/json"}, json={"ok": True}
    )


@pytest.mark.anyio
async def test_forward_request_streams_litellm_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ttft_logs: list[dict[str, object]] = []
    tracker = LLMGatewayLoadTracker()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy._proxy_load_tracker", tracker)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://litellm:4000/v1/messages"
        assert request.headers["Authorization"] == "Bearer llm-token"
        payload = orjson.loads(request.content)
        assert "reasoning_effort" not in payload
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                b'event: message_start\ndata: {"type":"message_start"}\n\n'
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            ),
        )

    monotonic_values = iter([10.0, 10.025, 10.05, 10.075, 10.1, 10.125])
    monkeypatch.setattr(
        "tracecat.agent.sandbox.llm_proxy.time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values, 10.125)),
    )

    def fake_info(message: str, **kwargs: object) -> None:
        if message == "LLM proxy first response chunk":
            ttft_logs.append(dict(kwargs))

    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger.info", fake_info)

    response_text, _ = await _forward(
        tmp_path,
        handler,
        _request({"stream": True, "messages": _HELLO}, headers=_LLM_HEADERS),
    )

    assert response_text.startswith("HTTP/1.1 200 OK")
    assert "text/event-stream" in response_text
    assert "event: message_start" in response_text
    assert "event: message_stop" in response_text
    assert len(ttft_logs) == 1
    assert ttft_logs[0]["request_counter"] == 1
    assert ttft_logs[0]["method"] == "POST"
    assert ttft_logs[0]["path"] == "/v1/messages"
    assert isinstance(ttft_logs[0]["trace_request_id"], str)
    assert ttft_logs[0]["ttft_ms"] == pytest.approx(25.0)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH", "CONNECT"])
async def test_forward_request_rejects_non_inference_methods(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
) -> None:
    """The socket proxy must not forward destructive/admin methods to the gateway.

    The socket is reachable by untrusted in-jail code; inference is POST and
    catalog discovery is GET. Anything else gets a 405 without leaving the
    proxy, so host-attached gateway credentials can never drive destructive
    or administrative endpoints.
    """
    tracker = LLMGatewayLoadTracker()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy._proxy_load_tracker", tracker)

    forwarded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(200)

    response_text, _ = await _forward(
        tmp_path, handler, _request(b"{}", method=method, headers={})
    )

    assert forwarded == [], "denied method must not reach the gateway"
    assert response_text.startswith("HTTP/1.1 405")
    assert "HTTP method not allowed" in response_text


@pytest.mark.anyio
async def test_forward_request_returns_upstream_error_response(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://litellm:4000/v1/messages/count_tokens"
        return httpx.Response(
            400,
            headers={"Content-Type": "application/json"},
            json={"error": "bad request"},
        )

    response_text, errors = await _forward(
        tmp_path,
        handler,
        _request(
            {"messages": _HELLO},
            path="/v1/messages/count_tokens",
            headers=_LLM_HEADERS,
        ),
    )

    assert response_text.startswith("HTTP/1.1 400 Bad Request")
    assert "X-Request-ID:" in response_text
    assert "bad request" in response_text
    assert errors == []


@pytest.mark.anyio
async def test_forward_request_emits_error_for_critical_upstream_http_error(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://litellm:4000/v1/messages"
        return httpx.Response(
            429,
            headers={"Content-Type": "application/json"},
            json={"error": {"message": "provider quota exhausted"}},
        )

    response_text, errors = await _forward(
        tmp_path,
        handler,
        _request(
            {"messages": _HELLO},
            headers={**_LLM_HEADERS, "X-Request-ID": "trace-test-123"},
        ),
    )

    assert response_text.startswith("HTTP/1.1 429 Too Many Requests")
    assert "provider quota exhausted" in response_text
    assert len(errors) == 1
    error = errors[0]
    assert error.message == "LLM requests are temporarily rate limited; retry later"
    assert "provider quota exhausted" not in error.message
    assert error.classification.owner is RuntimeErrorOwner.PLATFORM
    assert error.classification.kind is RuntimeErrorKind.AGENT_LLM_RATE_LIMITED
    assert error.classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
async def test_forward_request_classifies_connect_failure_as_platform(
    tmp_path: Path,
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("gateway unavailable", request=request)

    _, errors = await _forward(tmp_path, handler, _request())

    # Nothing was sent, so the managed route retries the connect once.
    assert attempts == 2
    assert len(errors) == 1
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED


_ROUTE_FAILURE_ATTRIBUTION = pytest.mark.parametrize(
    ("direct", "expected_owner", "expected_kind"),
    [
        (
            False,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED,
        ),
        (True, RuntimeErrorOwner.USER, RuntimeErrorKind.AGENT_EXECUTION_FAILED),
    ],
)


def _direct_or_managed(direct: bool) -> tuple[ParsedRequest, LLMRoutingPlan]:
    if direct:
        return _request(b'{"model":"customer-alias","messages":[]}'), _direct_plan()
    return _request(), _routing_plan()


@pytest.mark.anyio
@_ROUTE_FAILURE_ATTRIBUTION
async def test_forward_request_classifies_error_body_read_failure_by_route(
    tmp_path: Path,
    direct: bool,
    expected_owner: RuntimeErrorOwner,
    expected_kind: RuntimeErrorKind,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            request=request,
            stream=_FailingResponseStream(request),
        )

    request, plan = _direct_or_managed(direct)
    response_text, errors = await _forward(
        tmp_path, handler, request, routing_plan=plan
    )

    assert response_text.startswith("HTTP/1.1 502 ")
    assert len(errors) == 1
    assert errors[0].classification.owner is expected_owner
    assert errors[0].classification.kind is expected_kind
    assert errors[0].classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
@_ROUTE_FAILURE_ATTRIBUTION
async def test_forward_request_does_not_write_second_response_after_body_failure(
    tmp_path: Path,
    direct: bool,
    expected_owner: RuntimeErrorOwner,
    expected_kind: RuntimeErrorKind,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Length": "100"},
            request=request,
            stream=_FailingResponseStream(request),
        )

    request, plan = _direct_or_managed(direct)
    response_text, errors = await _forward(
        tmp_path, handler, request, routing_plan=plan
    )

    assert response_text.startswith("HTTP/1.1 200 OK")
    assert response_text.count("HTTP/1.1") == 1
    assert len(errors) == 1
    assert errors[0].classification.owner is expected_owner
    assert errors[0].classification.kind is expected_kind
    assert errors[0].classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["connect", "timeout"])
async def test_forward_request_classifies_direct_transport_failure(
    tmp_path: Path,
    failure: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://customer-provider.example/v1/messages"
        if failure == "connect":
            raise httpx.ConnectError("provider unavailable", request=request)
        raise httpx.ReadTimeout("provider timed out", request=request)

    request, plan = _direct_or_managed(True)
    _, errors = await _forward(tmp_path, handler, request, routing_plan=plan)

    assert len(errors) == 1
    assert errors[0].classification.owner is (
        RuntimeErrorOwner.PLATFORM if failure == "timeout" else RuntimeErrorOwner.USER
    )
    assert errors[0].classification.kind is (
        RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT
        if failure == "timeout"
        else RuntimeErrorKind.AGENT_EXECUTION_FAILED
    )
    assert errors[0].classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
async def test_write_stream_response_emits_error_after_headers_sent(
    tmp_path: Path,
) -> None:
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()

    async def broken_stream() -> AsyncIterator[bytes]:
        yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
        raise RuntimeError("provider stream disconnected")

    await socket_proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": "text/event-stream"},
        body_chunks=broken_stream(),
        trace_request_id="trace-test-456",
        path="/v1/messages",
    )

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")
    assert "event: error" in response_text
    assert "provider stream disconnected" in response_text
    assert [error.message for error in errors] == [
        "LLM stream failed: provider stream disconnected"
    ]
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_type", "kind", "owner"),
    [
        (
            "throttling_error",
            RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
            RuntimeErrorOwner.PLATFORM,
        ),
        (
            "budget_exceeded",
            RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
            RuntimeErrorOwner.USER,
        ),
    ],
)
async def test_write_stream_response_classifies_typed_upstream_error_event(
    tmp_path: Path,
    error_type: str,
    kind: RuntimeErrorKind,
    owner: RuntimeErrorOwner,
) -> None:
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()
    error_event = b"event: error\ndata: " + orjson.dumps(
        {
            "type": "error",
            "error": {"type": error_type, "message": "synthetic provider error"},
        }
    )

    async def upstream() -> AsyncIterator[bytes]:
        yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
        yield error_event + b"\n\n"

    await socket_proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": "text/event-stream"},
        body_chunks=llm_proxy._rewrite_sse_stream(upstream(), ToolUseStreamRewriter()),
        trace_request_id="trace-test-upstream-error",
        path="/v1/messages",
    )

    assert writer.buffer.count(b"event: error") == 1
    assert error_event in writer.buffer
    assert [error.message for error in errors] == [
        "LLM stream failed: synthetic provider error"
    ]
    assert errors[0].classification.kind is kind
    assert errors[0].classification.owner is owner


@pytest.mark.anyio
async def test_write_stream_response_forwards_untyped_upstream_error_event(
    tmp_path: Path,
) -> None:
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()
    error_event = (
        b'event: error\ndata: {"type":"error","error":'
        b'{"type":"overloaded_error","message":"synthetic"}}\n\n'
    )

    async def upstream() -> AsyncIterator[bytes]:
        yield error_event

    await socket_proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": "text/event-stream"},
        body_chunks=llm_proxy._rewrite_sse_stream(upstream(), ToolUseStreamRewriter()),
        trace_request_id="trace-test-overloaded",
        path="/v1/messages",
    )

    assert writer.buffer.endswith(error_event)
    assert errors == []


@pytest.mark.anyio
async def test_write_stream_response_surfaces_friendly_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.sandbox.llm_proxy.app_config.TRACECAT__LLM_PROXY_READ_TIMEOUT",
        600.0,
    )
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()

    async def timed_out_stream() -> AsyncIterator[bytes]:
        yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
        raise httpx.ReadTimeout("")

    await socket_proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": "text/event-stream"},
        body_chunks=timed_out_stream(),
        trace_request_id="trace-test-timeout",
        path="/v1/messages",
    )

    expected_message = (
        "Model response timed out after 10 minutes without receiving data. "
        "Please retry the agent execution."
    )
    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")
    assert "event: error" in response_text
    assert expected_message in response_text
    assert [error.message for error in errors] == [expected_message]
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["disconnect", "timeout"])
async def test_write_stream_response_keeps_direct_transport_failure_retryable(
    tmp_path: Path,
    failure: str,
) -> None:
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()

    async def broken_stream() -> AsyncIterator[bytes]:
        yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
        request = httpx.Request("POST", "https://customer-provider.example")
        if failure == "timeout":
            raise httpx.ReadTimeout("provider timed out", request=request)
        raise httpx.ReadError("provider disconnected", request=request)

    await socket_proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": "text/event-stream"},
        body_chunks=broken_stream(),
        trace_request_id="trace-test-direct-transport",
        path="/v1/messages",
        route_is_direct=True,
    )

    assert len(errors) == 1
    assert errors[0].classification.owner is (
        RuntimeErrorOwner.PLATFORM if failure == "timeout" else RuntimeErrorOwner.USER
    )
    assert errors[0].classification.kind is (
        RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT
        if failure == "timeout"
        else RuntimeErrorKind.AGENT_EXECUTION_FAILED
    )
    assert errors[0].classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
@pytest.mark.parametrize("content_type", ["application/json", "text/event-stream"])
async def test_write_response_ignores_transport_failure_after_client_disconnect(
    tmp_path: Path,
    content_type: str,
) -> None:
    errors: list[LLMProxyError] = []
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    writer = _FakeWriter()

    async def disconnected_stream() -> AsyncIterator[bytes]:
        writer.close()
        request = httpx.Request("POST", "http://litellm:4000/v1/messages")
        raise httpx.ReadError("provider disconnected", request=request)
        yield b""  # pragma: no cover

    await socket_proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": content_type},
        body_chunks=disconnected_stream(),
        trace_request_id="trace-test-client-disconnect",
        path="/v1/messages",
    )

    assert writer.buffer.count(b"HTTP/1.1") == 1
    assert errors == []


@pytest.mark.anyio
async def test_forward_request_strips_authorization_for_passthrough_upstream(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://customer-litellm.example/v1/messages"
        assert "authorization" not in request.headers
        return _ok(request)

    response_text, _ = await _forward(
        tmp_path,
        handler,
        _request({"model": "customer-alias", "messages": _HELLO}, headers=_LLM_HEADERS),
        routing_plan=_direct_plan("https://customer-litellm.example"),
    )

    assert response_text.startswith("HTTP/1.1 200 OK")


def test_direct_route_strips_version_suffix(tmp_path: Path) -> None:
    """Passthrough strips a trailing /vN from the stored base_url so it does
    not collide with the /v1/... path the SDK clients emit."""
    routing_plan = _direct_plan("https://customer-litellm.example/v1/")

    assert (
        routing_plan.direct_routes["customer-alias"].base_url
        == "https://customer-litellm.example"
    )


def test_routing_plan_uses_managed_route_for_non_string_model() -> None:
    routing_plan = _direct_plan("https://customer-litellm.example")

    assert (
        routing_plan.resolve({"model": "customer-alias"}) is routing_plan.managed_route
    )


def test_managed_route_url_preserves_path(tmp_path: Path) -> None:
    """Non-passthrough talks to internal LiteLLM at a known host root and must
    not have any path segment trimmed."""
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(managed_url="http://litellm:4000/v1"),
    )

    assert socket_proxy.routing_plan.managed_route.base_url == "http://litellm:4000/v1"


@pytest.mark.anyio
async def test_passthrough_does_not_double_prefix_version_in_request_url(
    tmp_path: Path,
) -> None:
    """Customer base_url ending in /v1 + client path /v1/messages must not
    produce /v1/v1/messages, which the upstream rejects with 404."""
    received_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received_urls.append(str(request.url))
        return _ok(request)

    await _forward(
        tmp_path,
        handler,
        _request({"model": "customer-alias", "messages": _HELLO}, headers=_LLM_HEADERS),
        routing_plan=_direct_plan("https://customer-litellm.example/v1"),
    )

    assert received_urls == ["https://customer-litellm.example/v1/messages"], (
        f"expected single /v1 prefix, got {received_urls[0]!r} — "
        "double-prefix causes upstream 404 'model not found'"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direct_alias", "upstream_model_name", "managed_model", "expected_direct"),
    [
        pytest.param(
            "customer-alias",
            None,
            "openai/gpt-5-mini::tracecat-subagent::analyst",
            (
                "https://customer-litellm.example/v1/messages",
                "Bearer sk-customer",
                "customer-alias",
            ),
            id="root-direct-subagent-gateway",
        ),
        pytest.param(
            "child-alias::tracecat-subagent::analyst",
            "child-alias",
            "openai/gpt-5-mini::tracecat-subagent::critic",
            (
                "https://customer-litellm.example/v1/messages",
                "Bearer sk-customer",
                "child-alias",
            ),
            id="subagent-direct-other-subagent-gateway",
        ),
    ],
)
async def test_passthrough_routes_only_exact_direct_model_key(
    tmp_path: Path,
    direct_alias: str,
    upstream_model_name: str | None,
    managed_model: str,
    expected_direct: tuple[str, str, str],
) -> None:
    """Only the exact passthrough model key bypasses managed LiteLLM; a
    passthrough subagent gets its own route, independent of other subagents."""
    received: list[tuple[str, str | None, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = orjson.loads(request.content)
        received.append(
            (
                str(request.url),
                request.headers.get("authorization"),
                payload.get("model"),
            )
        )
        return _ok(request)

    plan = _direct_plan(
        "https://customer-litellm.example/v1",
        alias=direct_alias,
        authorization="Bearer sk-customer",
        upstream_model_name=upstream_model_name,
    )
    for model in (direct_alias, managed_model):
        await _forward(
            tmp_path,
            handler,
            _request({"model": model, "messages": _HELLO}, headers=_LLM_HEADERS),
            routing_plan=plan,
        )

    assert received == [
        expected_direct,
        ("http://litellm:4000/v1/messages", "Bearer llm-token", managed_model),
    ]


_ANTHROPIC_ONLY_FIELDS: dict[str, object] = {
    "anthropic_beta": ["prompt-caching-2024-07-31"],
    "context_management": {"strategy": "summarize"},
    "output_config": {"task_budget": 2048},
    "output_format": {"type": "json_schema"},
}


@pytest.mark.anyio
async def test_forward_request_preserves_anthropic_fields_for_anthropic_upstream(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://customer-litellm.example/v1/messages"
        payload = orjson.loads(request.content)
        assert payload["messages"] == _HELLO
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert payload["reasoning_effort"] == "high"
        for field, value in _ANTHROPIC_ONLY_FIELDS.items():
            assert payload[field] == value
        return _ok(request)

    response_text, _ = await _forward(
        tmp_path,
        handler,
        _request(
            {
                "model": "claude-direct",
                "messages": _HELLO,
                "thinking": {"type": "enabled", "budget_tokens": 1024},
                "reasoning_effort": "high",
                **_ANTHROPIC_ONLY_FIELDS,
            },
            headers={"Content-Type": "application/json", "Content-Length": "112"},
        ),
        routing_plan=_direct_plan(
            "https://customer-litellm.example",
            alias="claude-direct",
            model_provider="anthropic",
        ),
    )

    assert response_text.startswith("HTTP/1.1 200 OK")


@pytest.mark.anyio
@pytest.mark.parametrize("local_provider_cleanup", [True, False])
async def test_managed_route_anthropic_only_field_cleanup(
    tmp_path: Path,
    local_provider_cleanup: bool,
) -> None:
    """Managed routes strip Anthropic-only fields locally unless the gateway
    owns provider cleanup."""

    path = "/v1/chat/completions" if local_provider_cleanup else "/v1/messages"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"http://litellm:4000{path}"
        payload = orjson.loads(request.content)
        for field, value in _ANTHROPIC_ONLY_FIELDS.items():
            if local_provider_cleanup:
                assert field not in payload
            else:
                assert payload[field] == value
        return _ok(request)

    response_text, _ = await _forward(
        tmp_path,
        handler,
        _request(
            {
                "model": "openai/gpt-5-mini::tracecat-subagent::analyst",
                "messages": _HELLO,
                **_ANTHROPIC_ONLY_FIELDS,
            },
            path=path,
            headers=_LLM_HEADERS,
        ),
        routing_plan=_routing_plan(
            managed_local_provider_cleanup=local_provider_cleanup
        ),
    )

    assert response_text.startswith("HTTP/1.1 200 OK")


@pytest.mark.anyio
async def test_forward_request_injects_passthrough_api_key_as_bearer_authorization(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-customer"
        return _ok(request)

    response_text, _ = await _forward(
        tmp_path,
        handler,
        _request({"model": "customer-alias", "messages": _HELLO}, headers=_LLM_HEADERS),
        routing_plan=_direct_plan(
            "https://customer-litellm.example", authorization="Bearer sk-customer"
        ),
    )

    assert response_text.startswith("HTTP/1.1 200 OK")


@pytest.mark.anyio
async def test_forward_request_short_circuits_event_logging_batch(
    tmp_path: Path,
) -> None:
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
    )
    writer = _FakeWriter()

    await socket_proxy._forward_request(
        {
            "method": "POST",
            "path": "/api/event_logging/batch",
            "headers": {"Content-Type": "application/json"},
            "body": b"{}",
        },
        cast(asyncio.StreamWriter, writer),
    )

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 204 No Content")


@pytest.mark.anyio
async def test_stop_closes_http_client_and_removes_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "llm.sock"
    socket_path.touch()
    socket_proxy = LLMSocketProxy(
        socket_path=socket_path,
        routing_plan=_routing_plan(),
    )
    socket_proxy._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(204, request=request),
        )
    )

    await socket_proxy.stop()

    assert socket_proxy._client is None
    assert not socket_path.exists()


def _custom_provider_route() -> LLMRoute:
    return LLMRoute(
        base_url="https://customer-gateway.example",
        model_provider="custom-model-provider",
    )


def test_direct_route_drops_anthropic_beta_header() -> None:
    route = _custom_provider_route()
    request = route.prepare_forward_request(
        path="/v1/messages",
        headers={
            "Content-Type": "application/json",
            "anthropic-beta": (
                "claude-code-20250219,interleaved-thinking-2025-05-14,"
                "advanced-tool-use-2025-11-20"
            ),
        },
        body=b"{}",
        data={},
    )
    assert "anthropic-beta" not in request.headers
    assert request.headers["Content-Type"] == "application/json"


def test_anthropic_route_preserves_beta_header() -> None:
    route = LLMRoute(
        base_url="https://customer-gateway.example",
        model_provider="anthropic",
    )
    request = route.prepare_forward_request(
        path="/v1/messages",
        headers={"anthropic-beta": "advanced-tool-use-2025-11-20"},
        body=b"{}",
        data={},
    )
    assert request.headers["anthropic-beta"] == "advanced-tool-use-2025-11-20"


def test_direct_route_forwards_tools_untouched() -> None:
    # Tool defs flow raw, including tool-search fields like defer_loading —
    # verified tolerated by LiteLLM and Ollama Anthropic-compat endpoints.
    route = _custom_provider_route()
    data = {
        "model": "claude-4-6-sonnet",
        "tools": [
            {
                "name": "mcp__tracecat-registry__core__cases__list_cases",
                "description": "List cases",
                "input_schema": {"type": "object"},
                "defer_loading": True,
            }
        ],
    }
    request = route.prepare_forward_request(
        path="/v1/messages",
        headers={},
        body=orjson.dumps(data),
        data=data,
    )
    forwarded = orjson.loads(request.body)
    assert forwarded["tools"] == data["tools"]


def test_direct_route_forwards_messages_untouched() -> None:
    # Message content is never rewritten — tool_reference blocks flow raw
    # (verified tolerated by LiteLLM and Ollama Anthropic-compat endpoints).
    route = _custom_provider_route()
    data = {
        "model": "claude-4-6-sonnet",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "content": [
                            {
                                "type": "tool_reference",
                                "tool_name": "mcp__tracecat-registry__core__cases__list_cases",
                            },
                            {"type": "text", "text": "1 result"},
                        ],
                    }
                ],
            },
            {"role": "assistant", "content": "ok"},
        ],
    }
    request = route.prepare_forward_request(
        path="/v1/messages",
        headers={},
        body=orjson.dumps(data),
        data=data,
    )
    forwarded = orjson.loads(request.body)
    assert forwarded["messages"] == data["messages"]


def test_anthropic_route_preserves_tool_reference_blocks_and_tool_fields() -> None:
    route = LLMRoute(
        base_url="https://customer-gateway.example",
        model_provider="anthropic",
    )
    data = {
        "model": "claude-direct",
        "tools": [{"name": "t", "input_schema": {}, "defer_loading": True}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "content": [{"type": "tool_reference", "tool_name": "t"}],
                    }
                ],
            }
        ],
    }
    request = route.prepare_forward_request(
        path="/v1/messages",
        headers={},
        body=orjson.dumps(data),
        data=data,
    )
    assert orjson.loads(request.body) == data


@pytest.mark.anyio
@pytest.mark.parametrize("direct", [True, False])
@pytest.mark.parametrize("body_started", [True, False])
@pytest.mark.parametrize("content_type", ["text/event-stream", "application/json"])
async def test_read_timeout_records_safe_body_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direct: bool,
    body_started: bool,
    content_type: str,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.sandbox.llm_proxy.app_config.TRACECAT__LLM_PROXY_READ_TIMEOUT",
        300.0,
    )
    errors: list[LLMProxyError] = []
    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    log = Mock()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger", log)
    now = 12.0
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.time.monotonic", lambda: now)

    async def stalled_body() -> AsyncIterator[bytes]:
        nonlocal now
        yield b""  # Empty chunks must not count as response data.
        if body_started:
            yield b"synthetic response content"
        now = 312.0
        raise httpx.ReadTimeout("synthetic sensitive diagnostic")

    writer = _FakeWriter()
    await proxy._write_response(
        cast(asyncio.StreamWriter, writer),
        status_code=200,
        reason_phrase="OK",
        headers={"Content-Type": content_type},
        body_chunks=stalled_body(),
        started_at=10.0,
        path="/v1/messages",
        route_is_direct=direct,
    )

    timeout_log = next(
        call
        for call in log.warning.call_args_list
        if call.args == ("LLM upstream read timed out",)
    )
    assert timeout_log.kwargs == {
        "route": "direct" if direct else "managed",
        "phase": "response_body",
        "read_timeout_seconds": 300.0,
        "response_body_started": body_started,
        "first_body_chunk_ms": 2000.0 if body_started else None,
        "time_since_last_chunk_ms": 300000.0 if body_started else None,
        "elapsed_ms": 302000.0,
    }
    assert len(errors) == 1
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert errors[0].classification.retry_disposition is RetryDisposition.RETRYABLE
    assert "synthetic sensitive diagnostic" not in str(errors)
    assert "synthetic sensitive diagnostic" not in str(log.warning.call_args_list)
    assert writer.buffer.count(b"HTTP/1.1") == 1


@pytest.mark.anyio
@pytest.mark.parametrize("direct", [True, False])
@pytest.mark.parametrize("error_response", [True, False])
async def test_read_timeout_before_headers_records_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direct: bool,
    error_response: bool,
) -> None:
    log = Mock()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger", log)

    class StalledErrorBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"partial error body"
            raise httpx.ReadTimeout("synthetic sensitive diagnostic")

    async def handler(request: httpx.Request) -> httpx.Response:
        if error_response:
            return httpx.Response(503, stream=StalledErrorBody())
        raise httpx.ReadTimeout("synthetic sensitive diagnostic", request=request)

    response_text, errors = await _forward(
        tmp_path,
        handler,
        _request(b'{"model":"synthetic-model"}', headers={}),
        routing_plan=_direct_plan("https://provider.example", alias="synthetic-model")
        if direct
        else _routing_plan(),
    )
    timeout_log = next(
        call
        for call in log.warning.call_args_list
        if call.args == ("LLM upstream read timed out",)
    )
    assert timeout_log.kwargs["route"] == ("direct" if direct else "managed")
    assert timeout_log.kwargs["phase"] == (
        "error_body" if error_response else "response_headers"
    )
    assert timeout_log.kwargs["response_body_started"] is (
        None if error_response else False
    )
    assert timeout_log.kwargs["first_body_chunk_ms"] is None
    assert len(errors) == 1
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert "synthetic sensitive diagnostic" not in str(errors)
    assert response_text.startswith("HTTP/1.1 504")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "error_type", "direct", "kind", "owner", "retryable"),
    [
        (
            401,
            "tracecat_llm_token_invalid",
            False,
            RuntimeErrorKind.AGENT_LLM_GATEWAY_AUTH_FAILED,
            RuntimeErrorOwner.PLATFORM,
            False,
        ),
        (
            401,
            "tracecat_llm_provider_auth_failed",
            False,
            RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            403,
            "tracecat_llm_model_not_enabled",
            False,
            RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            403,
            "tracecat_llm_provider_subscription_required",
            False,
            RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            401,
            "tracecat_llm_token_invalid",
            True,
            RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            429,
            "budget_exceeded",
            False,
            RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            400,
            "budget_exceeded",
            False,
            RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            429,
            "budget_exceeded",
            True,
            RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            429,
            "rate_limit_error",
            False,
            RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
            RuntimeErrorOwner.PLATFORM,
            True,
        ),
        (
            429,
            "cooldown",
            False,
            RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
            RuntimeErrorOwner.PLATFORM,
            True,
        ),
        (
            401,
            "auth_error",
            False,
            RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED,
            RuntimeErrorOwner.PLATFORM,
            True,
        ),
        (
            500,
            "tracecat_llm_token_invalid",
            False,
            RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED,
            RuntimeErrorOwner.PLATFORM,
            True,
        ),
        (
            504,
            "api_error",
            False,
            RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT,
            RuntimeErrorOwner.PLATFORM,
            True,
        ),
        (
            403,
            "api_error",
            True,
            RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            429,
            "rate_limit_error",
            True,
            RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
            RuntimeErrorOwner.USER,
            True,
        ),
        (
            503,
            "api_error",
            True,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            RuntimeErrorOwner.USER,
            False,
        ),
        (
            408,
            "api_error",
            True,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            RuntimeErrorOwner.USER,
            True,
        ),
        (
            504,
            "api_error",
            True,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            RuntimeErrorOwner.USER,
            True,
        ),
    ],
)
async def test_structured_gateway_http_attribution(
    tmp_path: Path,
    status_code: int,
    error_type: str,
    direct: bool,
    kind: RuntimeErrorKind,
    owner: RuntimeErrorOwner,
    retryable: bool,
) -> None:
    # Deliberately misleading text must never select classification, and
    # credential-shaped data must never enter durable failure text.
    body = orjson.dumps(
        {
            "error": {
                "type": error_type,
                "message": "budget_exceeded invalid JWT Bearer synthetic-secret",
            }
        }
    )
    response_text, errors = await _forward(
        tmp_path,
        lambda request: httpx.Response(status_code, content=body),
        _request(b'{"model":"synthetic-model"}', headers={}),
        routing_plan=_direct_plan("https://provider.example", alias="synthetic-model")
        if direct
        else _routing_plan(),
    )
    assert len(errors) == 1
    assert errors[0].classification.kind is kind
    assert errors[0].classification.owner is owner
    assert errors[0].classification.retry_disposition is (
        RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
    )
    assert "synthetic-secret" not in errors[0].message
    # SDK still receives the upstream HTTP response.
    assert body.decode() in response_text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "failure"),
    [
        ("/models", "auth"),
        ("/v1/models?limit=10", "rate_limit"),
        ("/models", "connect"),
        ("/v1/models?limit=10", "headers_timeout"),
        ("/models", "body_timeout"),
    ],
)
async def test_failed_discovery_does_not_consume_generation_failure_callback(
    tmp_path: Path,
    path: str,
    failure: str,
) -> None:
    errors: list[LLMProxyError] = []

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"data":'
            raise httpx.ReadTimeout("synthetic discovery timeout")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                401, json={"error": {"type": "tracecat_llm_token_invalid"}}
            )
        if failure == "connect":
            raise httpx.ConnectError("synthetic connection failure")
        if failure == "headers_timeout":
            raise httpx.ReadTimeout("synthetic discovery timeout")
        if failure == "body_timeout":
            return httpx.Response(200, stream=StalledBody())
        return httpx.Response(
            401 if failure == "auth" else 429, json={"error": "synthetic failure"}
        )

    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy._client = client
        proxy._direct_client = client
        await proxy._forward_request(
            {"method": "GET", "path": path, "headers": {}, "body": b""},
            cast(asyncio.StreamWriter, _FakeWriter()),
        )
        assert errors == []
        await proxy._forward_request(
            {"method": "POST", "path": "/v1/messages", "headers": {}, "body": b"{}"},
            cast(asyncio.StreamWriter, _FakeWriter()),
        )
    assert len(errors) == 1
    assert (
        errors[0].classification.kind is RuntimeErrorKind.AGENT_LLM_GATEWAY_AUTH_FAILED
    )


@pytest.mark.parametrize(
    ("body", "expected_kind"),
    [
        (
            b'{"error":{"code":"insufficient_quota"}}',
            RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
        ),
        (
            b'{"error":{"type":"insufficient_quota"}}',
            RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED,
        ),
        (
            b'{"error":{"message":"budget_exceeded insufficient_quota"}}',
            RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
        ),
        (
            b'{"error":{"type":["budget_exceeded"]}}',
            RuntimeErrorKind.AGENT_LLM_RATE_LIMITED,
        ),
        (b"budget_exceeded", RuntimeErrorKind.AGENT_LLM_RATE_LIMITED),
        (b"", RuntimeErrorKind.AGENT_LLM_RATE_LIMITED),
    ],
)
def test_budget_classification_requires_structured_evidence(
    body: bytes,
    expected_kind: RuntimeErrorKind,
) -> None:
    assert (
        _http_error_classification(429, route_is_direct=True, body=body).kind
        is expected_kind
    )


@pytest.mark.parametrize(
    ("status_code", "direct", "body", "model", "kind", "retryable", "message"),
    [
        pytest.param(
            400,
            False,
            b'{"error":{"type":"BadRequestError","message":"secret-ish detail"}}',
            "vendor/example-model",
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            False,
            "LLM provider rejected the request (HTTP 400, BadRequestError) "
            "for model vendor/example-model",
            id="safe-detail",
        ),
        pytest.param(
            400,
            False,
            b'{"error":{"type":"invalid_request_error","code":"unsupported_parameter"}}',
            None,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            False,
            "LLM provider rejected the request (HTTP 400, unsupported_parameter)",
            id="code-over-type",
        ),
        pytest.param(
            400,
            False,
            b'{"error":{"type":"sk-live-abc def with spaces!"}}',
            None,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            False,
            "LLM provider rejected the request (HTTP 400)",
            id="unsafe-token-dropped",
        ),
        pytest.param(
            503,
            False,
            b"",
            None,
            RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED,
            True,
            "Unclassified agent executor error: LLM gateway returned HTTP 503",
            id="managed-unclassified-no-body",
        ),
        pytest.param(
            504,
            True,
            b"",
            None,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            True,
            "LLM provider rejected the request (HTTP 504)",
            id="direct-timeout-status-retryable",
        ),
    ],
)
def test_generic_http_error_message(
    status_code: int,
    direct: bool,
    body: bytes,
    model: str | None,
    kind: RuntimeErrorKind,
    retryable: bool,
    message: str,
) -> None:
    classification = _http_error_classification(
        status_code, route_is_direct=direct, body=body, model=model
    )

    assert classification.kind is kind
    assert classification.retry_disposition is (
        RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
    )
    assert classification.message == message


def test_managed_unclassified_http_error_keeps_gateway_message() -> None:
    classification = _http_error_classification(
        500,
        route_is_direct=False,
        body=orjson.dumps(
            {
                "error": {
                    "type": "None",
                    "message": "Catalog row abc is not enabled for this workspace "
                    "Bearer synthetic-secret",
                }
            }
        ),
    )

    assert classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert classification.message.startswith(
        "Unclassified agent executor error: LLM gateway returned HTTP 500: "
        "Catalog row abc is not enabled for this workspace"
    )
    assert "synthetic-secret" not in classification.message


@pytest.mark.parametrize("oversized", [False, True])
def test_error_classification_bounds_json_parsing(
    monkeypatch: pytest.MonkeyPatch, oversized: bool
) -> None:
    body = b'{"error":{"code":"insufficient_quota"}}'.ljust(
        MAX_LLM_ERROR_BODY_BYTES + int(oversized), b" "
    )
    parse = Mock(wraps=orjson.loads)
    monkeypatch.setattr(orjson, "loads", parse)

    classification = _http_error_classification(429, route_is_direct=False, body=body)

    if oversized:
        parse.assert_not_called()
        assert classification.kind is RuntimeErrorKind.AGENT_LLM_RATE_LIMITED
    else:
        parse.assert_called_once_with(body)
        assert classification.kind is RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configuration", "direct", "failure"),
    [
        ("builtin", False, "auth"),
        ("custom", True, "auth"),
        ("custom", False, "budget"),
        ("builtin", True, "rate"),
        ("builtin", False, "connect"),
        ("custom", True, "connect"),
        ("custom", False, "headers_timeout"),
        ("builtin", True, "body_timeout"),
    ],
)
async def test_llm_metadata_follows_selected_route_on_all_failure_phases(
    tmp_path: Path,
    configuration: Literal["builtin", "custom"],
    direct: bool,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.config.TRACECAT__SERVICE_KEY", "synthetic-signing-key-for-tests-only"
    )

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"data: {}\n\n"
            raise httpx.ReadTimeout("synthetic timeout")

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("synthetic failure")
        if failure == "headers_timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if failure == "body_timeout":
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=StalledBody()
            )
        return httpx.Response(
            401 if failure == "auth" else 429,
            json={
                "error": {
                    "type": "budget_exceeded"
                    if failure == "budget"
                    else "tracecat_llm_token_invalid"
                    if failure == "auth"
                    else "rate_limit_error"
                }
            },
        )

    selected_route = LLMRoute(
        base_url="https://provider.example",
        model_provider="custom-model-provider"
        if configuration == "custom"
        else "openai",
        mode="direct" if direct else "managed",
    )
    plan = LLMRoutingPlan(
        managed_route=LLMRoute(
            base_url="http://gateway", model_provider="root-provider", mode="managed"
        ),
        direct_routes={"synthetic-model": selected_route} if direct else {},
    )
    token = mint_llm_token(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        model="synthetic-root",
        provider="openai",
        routes={
            "synthetic-model": LLMRouteClaim(
                model="synthetic-model",
                provider=(
                    "openai" if configuration == "custom" else "custom-model-provider"
                )
                if direct
                else selected_route.model_provider,
            )
        },
    )
    _, errors = await _forward(
        tmp_path,
        handler,
        _request(
            b'{"model":"synthetic-model"}', headers={"Authorization": f"Bearer {token}"}
        ),
        routing_plan=plan,
    )
    assert len(errors) == 1
    classification = errors[0].classification
    assert errors[0].diagnostic == LLMErrorDiagnostics(
        route="direct" if direct else "managed", provider_configuration=configuration
    )
    assert "provider.example" not in classification.model_dump_json()
    assert "synthetic-model" not in classification.model_dump_json()
    if failure in {"headers_timeout", "body_timeout"}:
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert classification.kind is RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT


@pytest.fixture
def short_socket_path() -> Iterator[Path]:
    # pytest's per-test directory can exceed macOS's Unix socket path limit.
    with TemporaryDirectory() as directory:
        yield Path(directory) / "llm.sock"


@pytest.mark.anyio
@pytest.mark.parametrize("fail_on_cancel", [False, True])
async def test_stop_cancels_stream_before_closing_client(
    short_socket_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_cancel: bool,
) -> None:
    errors: list[LLMProxyError] = []
    reading = asyncio.Event()
    stream_closed = asyncio.Event()
    client_closed = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"data: ready\n\n"
            reading.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if fail_on_cancel:
                    # Some transports surface a read error during cancellation.
                    raise httpx.ReadError("stream closed during teardown") from None
                raise

        async def aclose(self) -> None:
            assert not client_closed.is_set()
            stream_closed.set()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=BlockingStream(),
            )

        async def aclose(self) -> None:
            assert stream_closed.is_set()
            client_closed.set()

    client = httpx.AsyncClient(transport=Transport())
    client_kwargs: list[dict[str, object]] = []

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        client_kwargs.append(kwargs)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    proxy = LLMSocketProxy(short_socket_path, _routing_plan(), errors.append)
    await proxy.start()
    reader, writer = await asyncio.open_unix_connection(proxy.socket_path)
    try:
        # Managed connections must expire before LiteLLM's 5s uvicorn keep-alive.
        limits = client_kwargs[0]["limits"]
        assert isinstance(limits, httpx.Limits)
        assert limits.keepalive_expiry is not None
        assert limits.keepalive_expiry < 5.0
        writer.write(b"POST /v1/messages HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(reading.wait(), timeout=2)
        await asyncio.wait_for(proxy.stop(), timeout=2)
        response = await asyncio.wait_for(reader.read(), timeout=2)
        assert b"data: ready" in response
        assert b"event: error" not in response
        assert errors == []
        assert stream_closed.is_set()
        assert client_closed.is_set()
        assert not proxy._connection_tasks
        assert not proxy.socket_path.exists()
        await proxy.stop()
    finally:
        writer.close()
        await writer.wait_closed()
        await proxy.stop()


@pytest.mark.anyio
async def test_stop_closes_connection_canceled_before_handler_starts(
    tmp_path: Path,
) -> None:
    proxy = LLMSocketProxy(tmp_path / "llm.sock", _routing_plan())
    writer = _FakeWriter()
    proxy._accept_connection(asyncio.StreamReader(), cast(asyncio.StreamWriter, writer))

    await proxy.stop()

    assert writer.is_closing()
    assert not proxy._connection_tasks


@pytest.mark.anyio
async def test_restart_reports_active_stream_failure(short_socket_path: Path) -> None:
    errors: list[LLMProxyError] = []
    proxy = LLMSocketProxy(short_socket_path, _routing_plan(), errors.append)
    for _ in range(2):
        await proxy.start()
        try:
            writer = _FakeWriter()
            await proxy._write_response(
                cast(asyncio.StreamWriter, writer),
                status_code=200,
                reason_phrase="OK",
                headers={"Content-Type": "text/event-stream"},
                body_chunks=_FailingResponseStream(
                    httpx.Request("POST", "http://example.com")
                ),
                path="/v1/messages",
            )
            assert b"event: error" in writer.buffer
        finally:
            await proxy.stop()

    assert len(errors) == 2
    assert all(
        error.classification.owner == RuntimeErrorOwner.PLATFORM for error in errors
    )


@pytest.mark.anyio
async def test_successful_managed_request_skips_diagnostic_token_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = Mock(
        side_effect=AssertionError("Success must not decode diagnostic tokens")
    )
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.verify_llm_token", verify)
    await _forward(
        tmp_path,
        lambda request: httpx.Response(200, content=b"ok"),
        _request(
            b'{"model":"synthetic-model"}',
            headers={"Authorization": "Bearer synthetic-token"},
        ),
        routing_plan=LLMRoutingPlan(
            managed_route=LLMRoute(
                base_url="http://gateway", model_provider="openai", mode="managed"
            ),
            direct_routes={},
        ),
    )
    verify.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [True, False])
async def test_forward_request_normalizes_read_tool_use_before_sandbox(
    tmp_path: Path, streaming: bool
) -> None:
    """Malformed Read inputs are fixed in the response the CLI validates."""
    bad_input = {"file_path": "/skills/demo/references/api.md", "pages": ""}
    if streaming:
        upstream_body = (
            b'event: content_block_start\ndata: {"type":"content_block_start",'
            b'"index":0,"content_block":{"type":"tool_use","id":"toolu_1",'
            b'"name":"Read","input":{}}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta",'
            b'"index":0,"delta":{"type":"input_json_delta","partial_json":'
            + orjson.dumps(orjson.dumps(bad_input).decode())
            + b"}}\n\n"
            b'event: content_block_stop\ndata: {"type":"content_block_stop",'
            b'"index":0}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        content_type = "text/event-stream"
    else:
        upstream_body = orjson.dumps(
            {
                "type": "message",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": bad_input,
                    }
                ],
            }
        )
        content_type = "application/json"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": content_type}, content=upstream_body
        )

    response_text, _ = await _forward(
        tmp_path, handler, _request({"stream": streaming, "messages": []})
    )

    head, _, body = response_text.encode().partition(b"\r\n\r\n")
    assert b"HTTP/1.1 200" in head
    assert b'"pages"' not in body
    assert b"/skills/demo/references/api.md" in body
    if not streaming:
        assert b"Content-Length" not in head
        payload = orjson.loads(body)
        assert payload["content"][0]["input"] == {
            "file_path": "/skills/demo/references/api.md"
        }


@pytest.mark.anyio
async def test_rewrite_json_body_passes_through_oversized_bodies() -> None:
    chunk = b"x" * (1024 * 1024)
    chunks = [chunk] * 12

    async def source() -> AsyncIterator[bytes]:
        for item in chunks:
            yield item

    out = [piece async for piece in _rewrite_json_body(source())]

    assert b"".join(out) == b"".join(chunks)
    # 11 chunks are flushed as one buffered piece; the 12th streams through.
    assert len(out) == 2


@pytest.mark.anyio
async def test_forward_request_retries_rate_limited_request_until_success(
    tmp_path: Path,
) -> None:
    bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) < 3:
            return httpx.Response(429, json={"error": {"type": "rate_limit_error"}})
        return httpx.Response(200, json={"ok": True})

    response_text, errors = await _forward(tmp_path, handler)

    assert response_text.startswith("HTTP/1.1 200 OK")
    assert '"ok":true' in response_text
    assert "rate_limit_error" not in response_text
    assert errors == []
    assert len(bodies) == 3
    assert len(set(bodies)) == 1


@pytest.mark.anyio
async def test_forward_request_emits_rate_limit_after_bounded_retries(
    tmp_path: Path,
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # A pooled socket the gateway closed on its keep-alive timer.
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=request
            )
        return httpx.Response(429, json={"error": {"type": "rate_limit_error"}})

    response_text, errors = await _forward(tmp_path, handler)

    # The pre-header connection drop is retried without using rate-limit budget.
    assert attempts == llm_proxy._RATE_LIMIT_MAX_ATTEMPTS + 1
    assert response_text.startswith("HTTP/1.1 429 Too Many Requests")
    assert response_text.count("HTTP/1.1") == 1
    assert len(errors) == 1
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_LLM_RATE_LIMITED


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (429, {"error": {"code": "insufficient_quota"}}),
        (429, {"error": {"type": "budget_exceeded"}}),
        (401, {"error": {"type": "auth_error"}}),
        (500, {"error": {"type": "internal"}}),
    ],
)
async def test_forward_request_does_not_retry_non_rate_limit_failures(
    tmp_path: Path,
    status_code: int,
    body: dict[str, object],
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, json=body)

    _, errors = await _forward(tmp_path, handler)

    assert attempts == 1
    assert len(errors) == 1
    assert errors[0].classification.kind is not RuntimeErrorKind.AGENT_LLM_RATE_LIMITED


def _rate_limited_retry_state(
    attempt_number: int, retry_after: str | None
) -> RetryCallState:
    retry_state = RetryCallState(AsyncRetrying(), None, (), {})
    retry_state.attempt_number = attempt_number
    error = _UpstreamRateLimited(retry_after)
    retry_state.set_exception((type(error), error, None))
    return retry_state


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("3", 3.0),
        ("0.5", 0.5),
        ("600", 20.0),
        ("0", 0.0),
    ],
)
def test_rate_limit_wait_honors_capped_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str,
    expected: float,
) -> None:
    monkeypatch.setattr(llm_proxy, "_RATE_LIMIT_MAX_DELAY_SECONDS", 20.0)

    assert _parse_retry_after(retry_after) == expected
    assert _rate_limit_wait(_rate_limited_retry_state(1, retry_after)) == expected


@pytest.mark.parametrize(
    "retry_after", [None, "Wed, 21 Oct 2015 07:28:00 GMT", "-1", "nan", "inf"]
)
def test_rate_limit_wait_falls_back_to_jittered_backoff(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
) -> None:
    monkeypatch.setattr(llm_proxy, "_RATE_LIMIT_BASE_DELAY_SECONDS", 1.0)
    monkeypatch.setattr(llm_proxy, "_RATE_LIMIT_MAX_DELAY_SECONDS", 20.0)

    assert _parse_retry_after(retry_after) is None
    assert 1.0 <= _rate_limit_wait(_rate_limited_retry_state(1, retry_after)) <= 1.5
    assert 4.0 <= _rate_limit_wait(_rate_limited_retry_state(3, retry_after)) <= 4.5
    assert _rate_limit_wait(_rate_limited_retry_state(10, retry_after)) == 20.0


_SSE_MESSAGE_START = b'event: message_start\ndata: {"type":"message_start"}\n\n'
_SSE_MESSAGE_STOP = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'


class _CutSSEStream(httpx.AsyncByteStream):
    """SSE body that dies after its first event, like a gateway pod going away."""

    def __init__(self, error: type[httpx.TransportError]) -> None:
        self._error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _SSE_MESSAGE_START
        raise self._error("peer closed connection without sending complete message")


@pytest.mark.anyio
@pytest.mark.parametrize("error", [httpx.RemoteProtocolError, httpx.ReadError])
async def test_managed_stream_cut_after_headers_is_stream_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: type[httpx.TransportError],
) -> None:
    log = Mock()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger", log)
    upstream_request_ids: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        upstream_request_ids.append(request.headers.get("x-request-id"))
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/event-stream",
                "X-Tracecat-Gateway": "litellm-0/42",
            },
            stream=_CutSSEStream(error),
        )

    response_text, errors = await _forward(
        tmp_path,
        handler,
        _request({"stream": True, "messages": _HELLO}, headers=_LLM_HEADERS),
    )

    # The partial response already reached the runtime: no transparent retry.
    assert len(upstream_request_ids) == 1
    assert response_text.startswith("HTTP/1.1 200 OK")
    assert response_text.count("HTTP/1.1") == 1
    assert "event: message_start" in response_text
    assert "event: error" in response_text
    # Raw transport text is not source-owned, so neither the runtime nor the
    # terminal error sees it.
    assert "peer closed connection" not in response_text
    assert len(errors) == 1
    classification = errors[0].classification
    assert errors[0].message == classification.message
    assert classification.kind is RuntimeErrorKind.AGENT_LLM_STREAM_INTERRUPTED
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert classification.cause_type == error.__name__

    stream_log = next(
        call
        for call in log.warning.call_args_list
        if call.args == ("Stream error after headers sent, emitting SSE error event",)
    )
    trace_request_id = upstream_request_ids[0]
    assert trace_request_id is not None
    assert f"X-Request-ID: {trace_request_id}" in response_text
    assert stream_log.kwargs["error_type"] == error.__name__
    assert stream_log.kwargs["route"] == "managed"
    assert stream_log.kwargs["trace_request_id"] == trace_request_id
    assert stream_log.kwargs["response_bytes"] == len(_SSE_MESSAGE_START)
    assert stream_log.kwargs["sse_events"] == 1
    assert stream_log.kwargs["saw_message_stop"] is False
    assert stream_log.kwargs["gateway_identity"] == "litellm-0/42"
    assert stream_log.kwargs["stream_duration_ms"] >= 0
    # Progress fields stay content-free.
    assert "hello" not in str(log.mock_calls)


@pytest.mark.anyio
async def test_direct_stream_cut_after_headers_keeps_user_attribution(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_CutSSEStream(httpx.RemoteProtocolError),
        )

    request, plan = _direct_or_managed(True)
    _, errors = await _forward(tmp_path, handler, request, routing_plan=plan)

    assert len(errors) == 1
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_EXECUTION_FAILED
    assert errors[0].classification.owner is RuntimeErrorOwner.USER


@pytest.mark.anyio
async def test_completed_stream_logs_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = Mock()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger", log)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_SSE_MESSAGE_START + _SSE_MESSAGE_STOP,
        )

    _, errors = await _forward(
        tmp_path,
        handler,
        _request(
            {"stream": True, "messages": _HELLO},
            headers={**_LLM_HEADERS, "X-Request-ID": "trace-complete"},
        ),
    )

    assert errors == []
    finished = next(
        call
        for call in log.info.call_args_list
        if call.args == ("LLM proxy stream finished",)
    )
    assert finished.kwargs["trace_request_id"] == "trace-complete"
    assert finished.kwargs["response_bytes"] == len(
        _SSE_MESSAGE_START + _SSE_MESSAGE_STOP
    )
    assert finished.kwargs["sse_events"] == 2
    assert finished.kwargs["saw_message_stop"] is True
    assert finished.kwargs["gateway_identity"] is None


@pytest.mark.anyio
async def test_forward_request_retries_managed_connect_error_once(
    tmp_path: Path,
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # A gateway pod that stopped listening before endpoints updated.
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"ok": True})

    response_text, errors = await _forward(tmp_path, handler)

    assert attempts == 2
    assert response_text.startswith("HTTP/1.1 200 OK")
    assert errors == []


@pytest.mark.anyio
async def test_forward_request_does_not_retry_direct_connect_error(
    tmp_path: Path,
) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused", request=request)

    request, plan = _direct_or_managed(True)
    _, errors = await _forward(tmp_path, handler, request, routing_plan=plan)

    assert attempts == 1
    assert len(errors) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direct", "inbound_headers", "expected"),
    [
        (False, {}, "generated"),
        (False, {"x-request-id": "trace-inbound"}, "trace-inbound"),
        (True, {}, None),
    ],
)
async def test_forward_request_sends_trace_request_id_to_gateway(
    tmp_path: Path,
    direct: bool,
    inbound_headers: dict[str, str],
    expected: str | None,
) -> None:
    sent: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get_list("x-request-id"))
        return _ok(request)

    request, plan = _direct_or_managed(direct)
    request = cast(
        ParsedRequest, {**request, "headers": {**_LLM_HEADERS, **inbound_headers}}
    )
    response_text, _ = await _forward(tmp_path, handler, request, routing_plan=plan)

    if expected is None:
        assert sent == [[]]
    elif expected == "generated":
        assert len(sent[0]) == 1
        assert f"X-Request-ID: {sent[0][0]}" in response_text
    else:
        assert sent == [[expected]]
