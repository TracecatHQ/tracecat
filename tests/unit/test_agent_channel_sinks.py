from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tracecat.agent.channels.sinks import SlackStreamSink
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent


@dataclass
class _FakeSlackResponse:
    data: dict[str, Any]


class _FakeSlackClient:
    calls: list[tuple[str, dict[str, Any]]]

    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = []

    async def api_call(self, *, api_method: str, params: dict[str, Any]) -> Any:
        self.calls.append((api_method, params))
        if api_method == "chat.startStream":
            return _FakeSlackResponse(data={"ts": "1234.56"})
        return _FakeSlackResponse(data={"ok": True})


@pytest.mark.anyio
async def test_slack_stream_sink_batches_deltas_and_stops(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr("tracecat.agent.channels.sinks.AsyncWebClient", _make_client)

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-1",
        workspace_id="workspace-1",
    )

    await sink.append(
        UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="Hello ")
    )
    await sink.append(UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="world"))
    await sink.done()

    methods = [method for method, _ in fake_client.calls]
    assert methods == ["chat.startStream", "chat.appendStream", "chat.stopStream"]

    _, append_params = fake_client.calls[1]
    assert append_params["markdown_text"] == "Hello world"

    _, stop_params = fake_client.calls[2]
    assert stop_params["metadata"] == {
        "event_type": "agent_session",
        "event_payload": {"session_id": "session-1"},
    }


@pytest.mark.anyio
async def test_slack_stream_sink_emits_error_text(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr("tracecat.agent.channels.sinks.AsyncWebClient", _make_client)

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-2",
        workspace_id="workspace-2",
    )

    await sink.error("boom")

    methods = [method for method, _ in fake_client.calls]
    assert methods == ["chat.startStream", "chat.appendStream", "chat.stopStream"]

    _, append_params = fake_client.calls[1]
    assert "Error: boom" in append_params["markdown_text"]
