from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast
from unittest.mock import Mock

import httpx
import orjson
import pytest

from tracecat.agent.diagnostics import MAX_LLM_ERROR_BODY_BYTES, LLMErrorDiagnostics
from tracecat.agent.observability import LLMGatewayLoadTracker
from tracecat.agent.sandbox.llm_proxy import (
    LLMProxyError,
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
    _http_error_classification,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)


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

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    monotonic_values = iter([10.0, 10.025, 10.05, 10.075, 10.1, 10.125])
    monkeypatch.setattr(
        "tracecat.agent.sandbox.llm_proxy.time.monotonic",
        lambda: next(monotonic_values, 10.125),
    )

    def fake_info(message: str, **kwargs: object) -> None:
        if message == "LLM proxy first response chunk":
            ttft_logs.append(dict(kwargs))

    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger.info", fake_info)

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": b'{"stream":true,"messages":[{"role":"user","content":"hello"}]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
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

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": method,
                "path": "/v1/messages",
                "headers": {},
                "body": b"{}",
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert forwarded == [], "denied method must not reach the gateway"
    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 405")
    assert "HTTP method not allowed" in response_text


@pytest.mark.anyio
async def test_forward_request_returns_upstream_error_response(
    tmp_path: Path,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://litellm:4000/v1/messages/count_tokens"
        return httpx.Response(
            400,
            headers={"Content-Type": "application/json"},
            json={"error": "bad request"},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages/count_tokens",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": b'{"messages":[{"role":"user","content":"hello"}]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 400 Bad Request")
    assert "X-Request-ID:" in response_text
    assert "bad request" in response_text
    assert errors == []


@pytest.mark.anyio
async def test_forward_request_emits_error_for_critical_upstream_http_error(
    tmp_path: Path,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://litellm:4000/v1/messages"
        return httpx.Response(
            429,
            headers={"Content-Type": "application/json"},
            json={"error": {"message": "provider quota exhausted"}},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                    "X-Request-ID": "trace-test-123",
                },
                "body": b'{"messages":[{"role":"user","content":"hello"}]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
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
@pytest.mark.parametrize(
    ("status_code", "expected_kind"),
    [
        (401, RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE),
        (403, RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE),
        (500, RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE),
        (504, RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT),
    ],
)
async def test_forward_request_classifies_platform_http_failures_at_source(
    tmp_path: Path,
    status_code: int,
    expected_kind: RuntimeErrorKind,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request, json={"error": "failure"})

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"Content-Type": "application/json"},
                "body": b'{"messages":[]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert len(errors) == 1
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert errors[0].classification.kind is expected_kind


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "expected_retry_disposition"),
    [
        (403, RetryDisposition.NON_RETRYABLE),
        (503, RetryDisposition.NON_RETRYABLE),
        (429, RetryDisposition.RETRYABLE),
        (408, RetryDisposition.RETRYABLE),
        (504, RetryDisposition.RETRYABLE),
    ],
)
async def test_forward_request_classifies_direct_provider_http_failure_as_user_owned(
    tmp_path: Path,
    status_code: int,
    expected_retry_disposition: RetryDisposition,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://customer-provider.example/v1/messages"
        return httpx.Response(
            status_code,
            request=request,
            json={"error": "unavailable"},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "customer-alias": LLMRoute(
                    base_url="https://customer-provider.example",
                    model_provider="custom-model-provider",
                )
            }
        ),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"Content-Type": "application/json"},
                "body": b'{"model":"customer-alias","messages":[]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert len(errors) == 1
    assert errors[0].classification.owner is RuntimeErrorOwner.USER
    expected_kind = (
        RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED
        if status_code == 403
        else RuntimeErrorKind.AGENT_LLM_RATE_LIMITED
        if status_code == 429
        else RuntimeErrorKind.AGENT_EXECUTION_FAILED
    )
    assert errors[0].classification.kind is expected_kind
    assert errors[0].classification.retry_disposition is expected_retry_disposition


@pytest.mark.anyio
async def test_forward_request_classifies_connect_failure_as_platform(
    tmp_path: Path,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gateway unavailable", request=request)

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"Content-Type": "application/json"},
                "body": b'{"messages":[]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert len(errors) == 1
    assert errors[0].classification.owner is RuntimeErrorOwner.PLATFORM
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direct", "expected_owner", "expected_kind"),
    [
        (
            False,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
        ),
        (True, RuntimeErrorOwner.USER, RuntimeErrorKind.AGENT_EXECUTION_FAILED),
    ],
)
async def test_forward_request_classifies_error_body_read_failure_by_route(
    tmp_path: Path,
    direct: bool,
    expected_owner: RuntimeErrorOwner,
    expected_kind: RuntimeErrorKind,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            request=request,
            stream=_FailingResponseStream(request),
        )

    direct_routes = (
        {
            "customer-alias": LLMRoute(
                base_url="https://customer-provider.example",
                model_provider="custom-model-provider",
            )
        }
        if direct
        else None
    )
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(direct_routes=direct_routes),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()
    body = b'{"model":"customer-alias","messages":[]}' if direct else b'{"messages":[]}'

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"Content-Type": "application/json"},
                "body": body,
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert writer.buffer.decode("utf-8").startswith("HTTP/1.1 502 ")
    assert len(errors) == 1
    assert errors[0].classification.owner is expected_owner
    assert errors[0].classification.kind is expected_kind
    assert errors[0].classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direct", "expected_owner", "expected_kind"),
    [
        (
            False,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
        ),
        (True, RuntimeErrorOwner.USER, RuntimeErrorKind.AGENT_EXECUTION_FAILED),
    ],
)
async def test_forward_request_does_not_write_second_response_after_body_failure(
    tmp_path: Path,
    direct: bool,
    expected_owner: RuntimeErrorOwner,
    expected_kind: RuntimeErrorKind,
) -> None:
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Length": "100"},
            request=request,
            stream=_FailingResponseStream(request),
        )

    direct_routes = (
        {
            "customer-alias": LLMRoute(
                base_url="https://customer-provider.example",
                model_provider="custom-model-provider",
            )
        }
        if direct
        else None
    )
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(direct_routes=direct_routes),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()
    body = b'{"model":"customer-alias","messages":[]}' if direct else b'{"messages":[]}'

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"Content-Type": "application/json"},
                "body": body,
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
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
    errors: list[LLMProxyError] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://customer-provider.example/v1/messages"
        if failure == "connect":
            raise httpx.ConnectError("provider unavailable", request=request)
        raise httpx.ReadTimeout("provider timed out", request=request)

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "customer-alias": LLMRoute(
                    base_url="https://customer-provider.example",
                    model_provider="custom-model-provider",
                )
            }
        ),
        on_error=errors.append,
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"Content-Type": "application/json"},
                "body": b'{"model":"customer-alias","messages":[]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

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
    assert errors[0].classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE


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
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "customer-alias": LLMRoute(
                    base_url="https://customer-litellm.example",
                    model_provider="custom-model-provider",
                )
            }
        ),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": (
                    b'{"model":"customer-alias",'
                    b'"messages":[{"role":"user","content":"hello"}]}'
                ),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")


def test_direct_route_strips_version_suffix(tmp_path: Path) -> None:
    """Passthrough strips a trailing /vN from the stored base_url so it does
    not collide with the /v1/... path the SDK clients emit."""
    routing_plan = _routing_plan(
        direct_routes={
            "customer-alias": LLMRoute(
                base_url="https://customer-litellm.example/v1/",
                model_provider="custom-model-provider",
            )
        }
    )

    assert (
        routing_plan.direct_routes["customer-alias"].base_url
        == "https://customer-litellm.example"
    )


def test_routing_plan_uses_managed_route_for_non_string_model() -> None:
    routing_plan = _routing_plan(
        direct_routes={
            "customer-alias": LLMRoute(
                base_url="https://customer-litellm.example",
                model_provider="custom-model-provider",
            )
        }
    )

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
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "customer-alias": LLMRoute(
                    base_url="https://customer-litellm.example/v1",
                    model_provider="custom-model-provider",
                )
            }
        ),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": (
                    b'{"model":"customer-alias",'
                    b'"messages":[{"role":"user","content":"hello"}]}'
                ),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert received_urls == ["https://customer-litellm.example/v1/messages"], (
        f"expected single /v1 prefix, got {received_urls[0]!r} — "
        "double-prefix causes upstream 404 'model not found'"
    )


@pytest.mark.anyio
async def test_passthrough_routes_root_direct_and_subagents_to_gateway(
    tmp_path: Path,
) -> None:
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
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "customer-alias": LLMRoute(
                    base_url="https://customer-litellm.example/v1",
                    model_provider="custom-model-provider",
                )
            },
            direct_authorizations={"customer-alias": "Bearer sk-customer"},
        ),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        # Only the exact passthrough model key should bypass managed LiteLLM.
        # Synthetic subagent keys without a direct route stay on the gateway.
        for model in (
            "customer-alias",
            "openai/gpt-5-mini::tracecat-subagent::analyst",
        ):
            writer = _FakeWriter()
            await socket_proxy._forward_request(
                {
                    "method": "POST",
                    "path": "/v1/messages",
                    "headers": {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer llm-token",
                    },
                    "body": orjson.dumps(
                        {
                            "model": model,
                            "messages": [{"role": "user", "content": "hello"}],
                        }
                    ),
                },
                cast(asyncio.StreamWriter, writer),
            )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert received == [
        (
            "https://customer-litellm.example/v1/messages",
            "Bearer sk-customer",
            "customer-alias",
        ),
        (
            "http://litellm:4000/v1/messages",
            "Bearer llm-token",
            "openai/gpt-5-mini::tracecat-subagent::analyst",
        ),
    ]


@pytest.mark.anyio
async def test_passthrough_routes_subagent_direct_when_subagent_config_passthrough(
    tmp_path: Path,
) -> None:
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
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    child_model = "child-alias::tracecat-subagent::analyst"
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                child_model: LLMRoute(
                    base_url="https://child-litellm.example/v1",
                    model_provider="custom-model-provider",
                    upstream_model_name="child-alias",
                )
            },
            direct_authorizations={child_model: "Bearer sk-child"},
        ),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        # A passthrough subagent gets its own direct route, independent of the
        # root agent and independent of other subagent routes.
        for model in (child_model, "openai/gpt-5-mini::tracecat-subagent::critic"):
            writer = _FakeWriter()
            await socket_proxy._forward_request(
                {
                    "method": "POST",
                    "path": "/v1/messages",
                    "headers": {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer llm-token",
                    },
                    "body": orjson.dumps(
                        {
                            "model": model,
                            "messages": [{"role": "user", "content": "hello"}],
                        }
                    ),
                },
                cast(asyncio.StreamWriter, writer),
            )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    assert received == [
        (
            "https://child-litellm.example/v1/messages",
            "Bearer sk-child",
            "child-alias",
        ),
        (
            "http://litellm:4000/v1/messages",
            "Bearer llm-token",
            "openai/gpt-5-mini::tracecat-subagent::critic",
        ),
    ]


@pytest.mark.anyio
async def test_forward_request_preserves_anthropic_fields_for_anthropic_upstream(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://customer-litellm.example/v1/messages"
        payload = orjson.loads(request.content)
        assert payload["messages"] == [{"role": "user", "content": "hello"}]
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert payload["reasoning_effort"] == "high"
        assert payload["anthropic_beta"] == ["prompt-caching-2024-07-31"]
        assert payload["context_management"] == {"strategy": "summarize"}
        assert payload["output_config"] == {"task_budget": 2048}
        assert payload["output_format"] == {"type": "json_schema"}
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "claude-direct": LLMRoute(
                    base_url="https://customer-litellm.example",
                    model_provider="anthropic",
                )
            }
        ),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Content-Length": "112",
                },
                "body": orjson.dumps(
                    {
                        "model": "claude-direct",
                        "messages": [{"role": "user", "content": "hello"}],
                        "thinking": {"type": "enabled", "budget_tokens": 1024},
                        "reasoning_effort": "high",
                        "anthropic_beta": ["prompt-caching-2024-07-31"],
                        "context_management": {"strategy": "summarize"},
                        "output_config": {"task_budget": 2048},
                        "output_format": {"type": "json_schema"},
                    }
                ),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")


@pytest.mark.anyio
async def test_forward_request_strips_anthropic_only_fields_for_non_anthropic_upstream(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = orjson.loads(request.content)
        assert "anthropic_beta" not in payload
        assert "context_management" not in payload
        assert "output_config" not in payload
        assert "output_format" not in payload
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": orjson.dumps(
                    {
                        "messages": [{"role": "user", "content": "hello"}],
                        "anthropic_beta": ["prompt-caching-2024-07-31"],
                        "context_management": {"strategy": "summarize"},
                        "output_config": {"task_budget": 2048},
                        "output_format": {"type": "json_schema"},
                    }
                ),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")


@pytest.mark.anyio
async def test_managed_route_can_defer_provider_cleanup_to_gateway(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = orjson.loads(request.content)
        assert str(request.url) == "http://litellm:4000/v1/messages"
        assert payload["anthropic_beta"] == ["prompt-caching-2024-07-31"]
        assert payload["context_management"] == {"strategy": "summarize"}
        assert payload["output_config"] == {"task_budget": 2048}
        assert payload["output_format"] == {"type": "json_schema"}
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(managed_local_provider_cleanup=False),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": orjson.dumps(
                    {
                        "model": "openai/gpt-5-mini::tracecat-subagent::analyst",
                        "messages": [{"role": "user", "content": "hello"}],
                        "anthropic_beta": ["prompt-caching-2024-07-31"],
                        "context_management": {"strategy": "summarize"},
                        "output_config": {"task_budget": 2048},
                        "output_format": {"type": "json_schema"},
                    }
                ),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")


@pytest.mark.anyio
async def test_forward_request_injects_passthrough_api_key_as_bearer_authorization(
    tmp_path: Path,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-customer"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"ok": True},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "customer-alias": LLMRoute(
                    base_url="https://customer-litellm.example",
                    model_provider="custom-model-provider",
                )
            },
            direct_authorizations={"customer-alias": "Bearer sk-customer"},
        ),
    )
    socket_proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": (
                    b'{"model":"customer-alias",'
                    b'"messages":[{"role":"user","content":"hello"}]}'
                ),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
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
    errors: list[LLMProxyError] = []
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

    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "synthetic-model": LLMRoute(
                    base_url="https://provider.example",
                    model_provider="custom-model-provider",
                )
            }
            if direct
            else None
        ),
        on_error=errors.append,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy._client = client
        writer = _FakeWriter()
        await proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": b'{"model":"synthetic-model"}',
            },
            cast(asyncio.StreamWriter, writer),
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
    assert writer.buffer.startswith(b"HTTP/1.1 504")


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
            RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
            RuntimeErrorOwner.PLATFORM,
            True,
        ),
        (
            500,
            "tracecat_llm_token_invalid",
            False,
            RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
            RuntimeErrorOwner.PLATFORM,
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
    errors: list[LLMProxyError] = []
    # Deliberately misleading text must never select classification or enter
    # durable failure text, even when it contains credential-shaped data.
    body = orjson.dumps(
        {
            "error": {
                "type": error_type,
                "message": "budget_exceeded invalid JWT Bearer synthetic-secret",
            }
        }
    )
    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        routing_plan=_routing_plan(
            direct_routes={
                "synthetic-model": LLMRoute(
                    base_url="https://provider.example",
                    model_provider="custom-model-provider",
                )
            }
            if direct
            else None
        ),
        on_error=errors.append,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, content=body)
        )
    ) as client:
        proxy._client = client
        writer = _FakeWriter()
        await proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": b'{"model":"synthetic-model"}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    assert len(errors) == 1
    assert errors[0].classification.kind is kind
    assert errors[0].classification.owner is owner
    assert errors[0].classification.retry_disposition is (
        RetryDisposition.RETRYABLE if retryable else RetryDisposition.NON_RETRYABLE
    )
    assert "synthetic-secret" not in errors[0].message
    assert body in writer.buffer  # SDK still receives the upstream HTTP response.


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/models", "/v1/models?limit=10"])
@pytest.mark.parametrize(
    "failure", ["auth", "rate_limit", "connect", "headers_timeout", "body_timeout"]
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
@pytest.mark.parametrize("configuration", ["builtin", "custom", None])
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize(
    "failure", ["auth", "budget", "rate", "connect", "headers_timeout", "body_timeout"]
)
async def test_llm_metadata_follows_selected_route_on_all_failure_phases(
    tmp_path: Path,
    configuration: Literal["builtin", "custom"] | None,
    direct: bool,
    failure: str,
) -> None:
    errors: list[LLMProxyError] = []

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
        model_provider="synthetic-provider",
        provider_configuration=configuration,
    )
    plan = LLMRoutingPlan(
        managed_route=LLMRoute(
            base_url="http://gateway", model_provider="root-provider", mode="managed"
        ),
        direct_routes={"synthetic-model": selected_route} if direct else {},
        managed_provider_configurations={"synthetic-model": configuration}
        if configuration is not None
        else {},
    )
    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock", routing_plan=plan, on_error=errors.append
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        proxy._client = client
        await proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": b'{"model":"synthetic-model"}',
            },
            cast(asyncio.StreamWriter, _FakeWriter()),
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
