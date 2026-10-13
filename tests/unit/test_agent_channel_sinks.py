from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tracecat import config
from tracecat.agent.channels.schemas import SlackChannelTokenConfig
from tracecat.agent.channels.sinks.session import (
    AgentStreamSink,
    FanoutStreamSink,
    open_session_event_sink,
)
from tracecat.agent.channels.sinks.slack import SlackStreamSink
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.session.types import AgentSessionEntity


@dataclass
class _FakeSlackResponse:
    data: dict[str, Any]


class _FakeSlackClient:
    calls: list[tuple[str, dict[str, Any]]]

    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = []

    async def api_call(
        self,
        *,
        api_method: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        payload = params if params is not None else (json or {})
        self.calls.append((api_method, payload))
        if api_method == "chat.startStream":
            return _FakeSlackResponse(data={"ts": "1234.56"})
        return _FakeSlackResponse(data={"ok": True})


class _FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        expire_seconds: int | None = None,
    ) -> bool:
        del expire_seconds
        self.values[key] = value
        return True


@pytest.fixture
def patched_slack_client(monkeypatch: pytest.MonkeyPatch) -> _FakeSlackClient:
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.AsyncWebClient", _make_client
    )
    return fake_client


@pytest.mark.anyio
async def test_slack_stream_sink_batches_deltas_and_stops(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

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
    assert append_params["chunks"] == [{"type": "markdown_text", "text": "Hello world"}]

    _, stop_params = fake_client.calls[2]
    assert json.loads(stop_params["metadata"]) == {
        "event_type": "agent_session",
        "event_payload": {"session_id": "session-1"},
    }


@pytest.mark.anyio
async def test_slack_stream_sink_emits_error_text(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

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
    error_chunk = append_params["chunks"][0]
    assert error_chunk["type"] == "markdown_text"
    assert "Error: boom" in error_chunk["text"]


@pytest.mark.anyio
async def test_slack_stream_sink_marks_reaction_complete(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        reaction_ts="1700000000.000001",
        session_id="session-3",
        workspace_id="workspace-3",
    )

    await sink.append(UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="Hi"))
    await sink.done()

    methods = [method for method, _ in fake_client.calls]
    assert "white_check_mark" in [
        payload.get("name")
        for method, payload in fake_client.calls
        if method == "reactions.add"
    ]
    assert methods.count("reactions.add") >= 2


@pytest.mark.anyio
async def test_slack_stream_sink_emits_task_updates_for_tool_events(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-4",
        workspace_id="workspace-4",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-1",
            tool_name="core.cases.create_case",
            tool_input={"summary": "hello"},
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-1",
            tool_name="core.cases.create_case",
            tool_output={"id": "case-123"},
            is_error=False,
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    markdown_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "markdown_text"
    ]
    statuses = [chunk["status"] for chunk in task_chunks]
    assert "complete" in statuses
    assert "in_progress" not in statuses
    assert all("output" not in chunk for chunk in task_chunks)
    assert not markdown_chunks


@pytest.mark.anyio
async def test_slack_stream_sink_does_not_close_on_text_stop(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-5",
        workspace_id="workspace-5",
    )

    await sink.append(UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="Hi"))
    await sink.append(UnifiedStreamEvent(type=StreamEventType.TEXT_STOP))
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-2",
            tool_name="core.cases.create_case",
            tool_input={"summary": "hello"},
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_updates = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert not task_updates


@pytest.mark.anyio
async def test_slack_stream_sink_formats_failure_for_any_tool(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-6",
        workspace_id="workspace-6",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-3",
            tool_name="core.cases.create_case",
            tool_output={"message": "case creation failed"},
            is_error=True,
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    error_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks")
        and payload["chunks"][0].get("type") == "task_update"
        and payload["chunks"][0].get("status") == "error"
    ]
    assert error_chunks
    error_chunk = error_chunks[0]
    assert error_chunk["details"] == "case creation failed"
    assert "output" not in error_chunk


@pytest.mark.anyio
async def test_slack_stream_sink_dedupes_in_progress_task_updates(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-7",
        workspace_id="workspace-7",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_call_id="tool-call-4",
            tool_name="core.cases.create_case",
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-4",
            tool_name="core.cases.create_case",
            tool_input={"summary": "hello"},
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert not task_chunks


@pytest.mark.anyio
async def test_slack_stream_sink_coalesces_in_progress_by_tool_name(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.AsyncWebClient", _make_client
    )

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-coalesce",
        workspace_id="workspace-coalesce",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-a",
            tool_name="core.cases.create_case",
            tool_input={"summary": "same"},
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-b",
            tool_name="core.cases.create_case",
            tool_input={"summary": "same"},
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert not task_chunks


@pytest.mark.anyio
async def test_slack_stream_sink_reuses_initial_id_for_same_tool_name(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.AsyncWebClient", _make_client
    )

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-signature",
        workspace_id="workspace-signature",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-x",
            tool_name="core.cases.create_case",
            tool_input={"summary": "first"},
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-y",
            tool_name="core.cases.create_case",
            tool_input={"summary": "second"},
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert not task_chunks


@pytest.mark.anyio
async def test_slack_stream_sink_skips_tool_updates_without_tool_call_id(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.AsyncWebClient", _make_client
    )

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-no-tool-call-id",
        workspace_id="workspace-no-tool-call-id",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_name="core.cases.create_case",
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_name="core.cases.create_case",
            tool_output={"id": "case-123"},
            is_error=False,
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_updates = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert not task_updates


@pytest.mark.anyio
async def test_slack_stream_sink_emits_approval_cards(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _FakeSlackClient(token="xoxb-test")
    fake_redis = _FakeRedisClient()

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    async def _get_redis() -> _FakeRedisClient:
        return fake_redis

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.AsyncWebClient", _make_client
    )
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.get_redis_client",
        _get_redis,
    )
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "test-signing-secret")

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-8",
        workspace_id="workspace-8",
    )

    await sink.append(
        UnifiedStreamEvent.approval_request_event(
            items=[
                ToolCallContent(
                    id="tool-call-approve-1",
                    name="core.http_request",
                    input={"url": "https://example.com", "method": "GET"},
                ),
                ToolCallContent(
                    id="tool-call-approve-2",
                    name="core.cases.create_case",
                    input={"summary": "hello"},
                ),
            ]
        )
    )
    await sink.done()

    post_messages = [
        payload for method, payload in fake_client.calls if method == "chat.postMessage"
    ]
    assert len(post_messages) == 2

    batch_entries = [
        json.loads(value)
        for key, value in fake_redis.values.items()
        if key.startswith("slack-approval:batch:")
    ]
    assert len(batch_entries) == 1
    batch = batch_entries[0]
    assert batch["session_id"] == "session-8"
    assert batch["workspace_id"] == "workspace-8"
    assert sorted(batch["tool_call_ids"]) == [
        "tool-call-approve-1",
        "tool-call-approve-2",
    ]


@pytest.mark.anyio
async def test_slack_stream_sink_ignores_pending_approval_interrupt_errors(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-approval-pending",
        workspace_id="workspace-approval-pending",
    )

    await sink.append(
        UnifiedStreamEvent.approval_request_event(
            items=[
                ToolCallContent(
                    id="tool-call-approval",
                    name="core.http_request",
                    input={"url": "https://example.com"},
                )
            ]
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-approval",
            tool_name="core.http_request",
            tool_output="Tool requires approval. Request sent for review.",
            is_error=True,
        )
    )
    await sink.done()

    task_chunks = [
        payload["chunks"][0]
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
        and payload.get("chunks")
        and payload["chunks"][0].get("type") == "task_update"
    ]
    statuses = [chunk.get("status") for chunk in task_chunks]

    assert "pending" in statuses
    assert "error" not in statuses


@pytest.mark.anyio
async def test_slack_stream_sink_skips_synthetic_approval_interrupt_error(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_client = _FakeSlackClient(token="xoxb-test")

    def _make_client(*, token: str) -> _FakeSlackClient:
        assert token == "xoxb-test"
        return fake_client

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.AsyncWebClient", _make_client
    )

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-9",
        workspace_id="workspace-9",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-interrupt-1",
            tool_name="core.cases.create_case",
            tool_output=(
                "The user doesn't want to take this action right now. "
                "STOP what you are doing and wait for the user to tell you how to proceed."
            ),
            is_error=True,
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_updates = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert not task_updates


@pytest.mark.anyio
async def test_slack_stream_sink_suppresses_output_for_all_tool_success(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-tool-success",
        workspace_id="workspace-tool-success",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="tool-call-success",
            tool_name="core.cases.create_case",
            tool_input={"summary": "hello"},
        )
    )
    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-success",
            tool_name="core.cases.create_case",
            tool_output={"id": "case-123"},
            is_error=False,
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert len(task_chunks) == 1
    assert task_chunks[0]["status"] == "complete"
    assert "output" not in task_chunks[0]


@pytest.mark.anyio
async def test_slack_stream_sink_formats_failure_for_slack_tool(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client

    sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id="session-slack-tool-failure",
        workspace_id="workspace-slack-tool-failure",
    )

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-slack-failure",
            tool_name="tools.slack.post_message",
            tool_output={"error": "channel_not_found"},
            is_error=True,
        )
    )
    await sink.done()

    append_payloads = [
        payload
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
    ]
    task_chunks = [
        payload["chunks"][0]
        for payload in append_payloads
        if payload.get("chunks") and payload["chunks"][0].get("type") == "task_update"
    ]
    assert len(task_chunks) == 1
    task_chunk = task_chunks[0]
    assert task_chunk["status"] == "error"
    assert task_chunk["details"] == "channel_not_found"
    assert "output" not in task_chunk


class _FakeSinkTarget:
    def __init__(self) -> None:
        self.append = AsyncMock()
        self.error = AsyncMock()
        self.done = AsyncMock()


class _FakeLookupResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeLookupSession:
    """Answers the session lookup, then the channel token lookup, in order."""

    def __init__(self, *rows: object) -> None:
        self._rows = list(rows)

    async def execute(self, _statement: object) -> _FakeLookupResult:
        return _FakeLookupResult(self._rows.pop(0))


@pytest.mark.anyio
async def test_session_sink_falls_back_to_redis_when_channel_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed channel lookup still streams the turn to Redis."""
    session_id, workspace_id, stream_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    external_lookup = AsyncMock(side_effect=SQLAlchemyError("database unavailable"))
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session._build_external_channel_sink",
        external_lookup,
    )
    redis_stream = _FakeSinkTarget()
    stream_new = AsyncMock(return_value=redis_stream)
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session.AgentStream.new", stream_new
    )

    sink = await open_session_event_sink(
        session_id=session_id, workspace_id=workspace_id, stream_id=stream_id
    )

    assert isinstance(sink, AgentStreamSink)
    assert sink.stream is redis_stream
    external_lookup.assert_awaited_once_with(
        session_id=session_id, workspace_id=workspace_id
    )
    stream_new.assert_awaited_once_with(
        session_id=session_id, workspace_id=workspace_id, stream_id=stream_id
    )


@pytest.mark.anyio
async def test_session_sink_sends_each_event_to_redis_and_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An external-channel session receives every event in Redis and the channel."""
    external_sink = _FakeSinkTarget()
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session._build_external_channel_sink",
        AsyncMock(return_value=external_sink),
    )
    redis_stream = _FakeSinkTarget()
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session.AgentStream.new",
        AsyncMock(return_value=redis_stream),
    )

    sink = await open_session_event_sink(
        session_id=uuid.uuid4(), workspace_id=uuid.uuid4(), stream_id=None
    )
    event = UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="hello")
    await sink.append(event)

    assert isinstance(sink, FanoutStreamSink)
    redis_stream.append.assert_awaited_once_with(event)
    external_sink.append.assert_awaited_once_with(event)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("entity_type", "fans_out"),
    [
        (AgentSessionEntity.EXTERNAL_CHANNEL, True),
        (AgentSessionEntity.CASE, False),
        (AgentSessionEntity.WORKFLOW, False),
    ],
)
async def test_session_sink_fans_out_only_for_slack_sessions(
    monkeypatch: pytest.MonkeyPatch,
    entity_type: AgentSessionEntity,
    fans_out: bool,
) -> None:
    """Only Slack-bound sessions stream to their thread; others use Redis alone."""
    preset_id = uuid.uuid4()
    agent_session = SimpleNamespace(
        entity_type=entity_type.value,
        channel_context={
            "channel_type": "slack",
            "channel_id": "C1",
            "thread_ts": "1700000000.100",
        },
        agent_preset_id=preset_id,
        entity_id=preset_id,
    )
    channel_token = SimpleNamespace(
        id=uuid.uuid4(),
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
    )
    lookup = _FakeLookupSession(agent_session, channel_token)

    @asynccontextmanager
    async def _session_context() -> AsyncIterator[_FakeLookupSession]:
        yield lookup

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session.get_async_session_context_manager",
        _session_context,
    )
    redis_stream = _FakeSinkTarget()
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session.AgentStream.new",
        AsyncMock(return_value=redis_stream),
    )

    sink = await open_session_event_sink(
        session_id=uuid.uuid4(), workspace_id=uuid.uuid4(), stream_id=uuid.uuid4()
    )

    if fans_out:
        assert isinstance(sink, FanoutStreamSink)
        redis_sink, channel_sink = sink.sinks
        assert isinstance(redis_sink, AgentStreamSink)
        assert redis_sink.stream is redis_stream
        assert isinstance(channel_sink, SlackStreamSink)
    else:
        assert isinstance(sink, AgentStreamSink)
        assert sink.stream is redis_stream
