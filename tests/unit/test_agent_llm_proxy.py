from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import httpx
import orjson
import pytest

from tracecat.agent.observability import LLMGatewayLoadTracker
from tracecat.agent.sandbox.llm_proxy import LLMSocketProxy


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
        upstream_url="http://litellm:4000",
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
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://litellm:4000/v1/messages/count_tokens"
        return httpx.Response(
            400,
            headers={"Content-Type": "application/json"},
            json={"error": "bad request"},
        )

    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        upstream_url="http://litellm:4000",
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
        upstream_url="https://customer-litellm.example",
        passthrough=True,
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
                "body": b'{"messages":[{"role":"user","content":"hello"}]}',
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if socket_proxy._client is not None:
            await socket_proxy._client.aclose()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")


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
        upstream_url="https://customer-litellm.example",
        passthrough=True,
        model_provider="anthropic",
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
        upstream_url="http://litellm:4000",
        model_provider="openai",
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
        upstream_url="https://customer-litellm.example",
        passthrough=True,
    )
    socket_proxy._upstream_api_key = "sk-customer"
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
                "body": b'{"messages":[{"role":"user","content":"hello"}]}',
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
        upstream_url="http://litellm:4000",
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
        upstream_url="http://litellm:4000",
    )
    socket_proxy._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(204, request=request),
        )
    )

    await socket_proxy.stop()

    assert socket_proxy._client is None
    assert not socket_path.exists()
