from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import orjson
import pytest

from tracecat.agent.observability import LLMGatewayLoadTracker
from tracecat.agent.sandbox.llm_proxy import (
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
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
async def test_forward_request_returns_upstream_error_response(
    tmp_path: Path,
) -> None:
    errors: list[str] = []

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
    errors: list[str] = []

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
    assert "LiteLLM request failed (429 Too Many Requests)" in errors[0]
    assert "Rate limit exceeded" in errors[0]
    assert "provider quota exhausted" in errors[0]
    assert "request_id=trace-test-123" in errors[0]


@pytest.mark.anyio
async def test_write_stream_response_emits_error_after_headers_sent(
    tmp_path: Path,
) -> None:
    errors: list[str] = []
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
    assert errors == ["LiteLLM stream failed: provider stream disconnected"]


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
