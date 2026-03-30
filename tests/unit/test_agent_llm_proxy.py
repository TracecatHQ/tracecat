from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tracecat.agent.llm_proxy.core import TracecatLLMProxy
from tracecat.agent.observability import LLMGatewayLoadTracker
from tracecat.agent.sandbox.llm_proxy import LLMSocketProxy
from tracecat.agent.tokens import LLMTokenClaims


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
async def test_forward_request_streams_tracecat_proxy_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    static_llm_proxy_factory,
) -> None:
    ttft_logs: list[dict[str, object]] = []
    tracker = LLMGatewayLoadTracker()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy._proxy_load_tracker", tracker)
    proxy = static_llm_proxy_factory({"OPENAI_API_KEY": "sk-test"})
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        tracecat_proxy=proxy,
    )
    writer = _FakeWriter()
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="openai",
        base_url=None,
        model_settings={},
        use_workspace_credentials=False,
    )

    monkeypatch.setattr(
        "tracecat.agent.llm_proxy.auth.verify_llm_token",
        lambda token: claims if token == "llm-token" else None,
    )
    monotonic_values = iter([10.0, 10.025, 10.05, 10.075, 10.1, 10.125])
    monkeypatch.setattr(
        "tracecat.agent.sandbox.llm_proxy.time.monotonic",
        lambda: next(monotonic_values, 10.125),
    )

    def fake_info(message: str, **kwargs: object) -> None:
        if message == "LLM proxy first response chunk":
            ttft_logs.append(dict(kwargs))

    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy.logger.info", fake_info)

    async def fake_bound_stream_messages(
        self: TracecatLLMProxy,
        *,
        payload: dict,
        claims: object,
        trace_request_id: str | None = None,
        ingress_headers: dict[str, str] | None = None,
    ) -> object:
        del self, claims, trace_request_id, ingress_headers
        assert payload["stream"] is True

        async def _events():
            yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

        return _events()

    monkeypatch.setattr(
        TracecatLLMProxy,
        "stream_messages",
        fake_bound_stream_messages,
    )

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
        await proxy.close()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 200 OK")
    assert "text/event-stream" in response_text
    assert "event: message_start" in response_text
    assert "event: message_stop" in response_text
    assert len(ttft_logs) == 1
    assert ttft_logs[0]["request_counter"] == 1
    assert ttft_logs[0]["backend"] == "tracecat_proxy"
    assert ttft_logs[0]["method"] == "POST"
    assert ttft_logs[0]["path"] == "/v1/messages"
    assert isinstance(ttft_logs[0]["trace_request_id"], str)
    assert ttft_logs[0]["ttft_ms"] == pytest.approx(25.0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "expected_detail"),
    [
        (b'{"messages":[', "Malformed JSON in request body"),
        (b'["not", "an", "object"]', "Request body must be a JSON object"),
    ],
)
async def test_forward_request_returns_bad_request_for_invalid_tracecat_proxy_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    static_llm_proxy_factory,
    body: bytes,
    expected_detail: str,
) -> None:
    proxy = static_llm_proxy_factory({"OPENAI_API_KEY": "sk-test"})
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        tracecat_proxy=proxy,
    )
    writer = _FakeWriter()
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="openai",
        base_url=None,
        model_settings={},
        use_workspace_credentials=False,
    )
    stream_messages = AsyncMock()

    monkeypatch.setattr(
        "tracecat.agent.llm_proxy.auth.verify_llm_token",
        lambda token: claims if token == "llm-token" else None,
    )
    monkeypatch.setattr(TracecatLLMProxy, "stream_messages", stream_messages)

    try:
        await socket_proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages/count_tokens",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer llm-token",
                },
                "body": body,
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        await proxy.close()

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 400")
    assert "X-Request-ID:" in response_text
    assert expected_detail in response_text
    stream_messages.assert_not_awaited()


@pytest.mark.anyio
async def test_stop_waits_for_tracecat_proxy_requests_to_finish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    static_llm_proxy_factory,
) -> None:
    events: list[str] = []
    proxy = static_llm_proxy_factory({"OPENAI_API_KEY": "sk-test"})
    socket_proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        tracecat_proxy=proxy,
    )

    async def fake_close() -> None:
        events.append("proxy_closed")

    monkeypatch.setattr(
        TracecatLLMProxy,
        "close",
        lambda self: fake_close(),
    )
    proxy.state.active_requests = 1

    async def finish_request() -> None:
        await asyncio.sleep(0.01)
        proxy.state.active_requests = 0
        events.append("request_finished")

    task = asyncio.create_task(finish_request())
    try:
        await socket_proxy.stop()
    finally:
        await task

    assert events == ["request_finished", "proxy_closed"]
