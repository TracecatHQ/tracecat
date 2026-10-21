from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from tracecat import config
from tracecat.agent.channels.sinks.slack import SlackStreamSink
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)


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


def _sink(session_id: str, **kwargs: str) -> SlackStreamSink:
    return SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        session_id=session_id,
        workspace_id=f"workspace-{session_id}",
        **kwargs,
    )


def _append_chunks(
    fake_client: _FakeSlackClient, chunk_type: str
) -> list[dict[str, Any]]:
    return [
        payload["chunks"][0]
        for method, payload in fake_client.calls
        if method == "chat.appendStream"
        and payload.get("chunks")
        and payload["chunks"][0].get("type") == chunk_type
    ]


def _tool_stop(tool_call_id: str, summary: str = "hello") -> UnifiedStreamEvent:
    return UnifiedStreamEvent(
        type=StreamEventType.TOOL_CALL_STOP,
        tool_call_id=tool_call_id,
        tool_name="core.cases.create_case",
        tool_input={"summary": summary},
    )


def _approval_request(*items: ToolCallContent) -> UnifiedStreamEvent:
    return UnifiedStreamEvent.approval_request_event(items=list(items))


@pytest.mark.anyio
async def test_slack_stream_sink_batches_deltas_and_stops(
    patched_slack_client: _FakeSlackClient,
):
    fake_client = patched_slack_client
    sink = _sink("session-1")

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
    sink = _sink("session-2")

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
    sink = _sink("session-3", reaction_ts="1700000000.000001")

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
    sink = _sink("session-4")

    await sink.append(_tool_stop("tool-call-1"))
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

    task_chunks = _append_chunks(fake_client, "task_update")
    assert len(task_chunks) == 1
    assert task_chunks[0]["status"] == "complete"
    assert "output" not in task_chunks[0]
    assert not _append_chunks(fake_client, "markdown_text")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "events",
    [
        pytest.param(
            [
                UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="Hi"),
                UnifiedStreamEvent(type=StreamEventType.TEXT_STOP),
                _tool_stop("tool-call-2"),
            ],
            id="text-stop-then-tool-stop",
        ),
        pytest.param(
            [
                UnifiedStreamEvent(
                    type=StreamEventType.TOOL_CALL_START,
                    tool_call_id="tool-call-4",
                    tool_name="core.cases.create_case",
                ),
                _tool_stop("tool-call-4"),
            ],
            id="tool-start-then-stop",
        ),
        pytest.param(
            [_tool_stop("tool-call-a", "same"), _tool_stop("tool-call-b", "same")],
            id="same-tool-same-input",
        ),
        pytest.param(
            [_tool_stop("tool-call-x", "first"), _tool_stop("tool-call-y", "second")],
            id="same-tool-different-input",
        ),
        pytest.param(
            [
                UnifiedStreamEvent(
                    type=StreamEventType.TOOL_CALL_START,
                    tool_name="core.cases.create_case",
                ),
                UnifiedStreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    tool_name="core.cases.create_case",
                    tool_output={"id": "case-123"},
                    is_error=False,
                ),
            ],
            id="missing-tool-call-id",
        ),
        pytest.param(
            [
                UnifiedStreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    tool_call_id="tool-call-interrupt-1",
                    tool_name="core.cases.create_case",
                    tool_output=(
                        "The user doesn't want to take this action right now. "
                        "STOP what you are doing and wait for the user to tell "
                        "you how to proceed."
                    ),
                    is_error=True,
                )
            ],
            id="synthetic-approval-interrupt-error",
        ),
    ],
)
async def test_slack_stream_sink_emits_no_in_progress_task_updates(
    patched_slack_client: _FakeSlackClient,
    events: list[UnifiedStreamEvent],
):
    sink = _sink("session-no-task-update")

    for event in events:
        await sink.append(event)
    await sink.done()

    assert not _append_chunks(patched_slack_client, "task_update")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "tool_output", "details"),
    [
        (
            "core.cases.create_case",
            {"message": "case creation failed"},
            "case creation failed",
        ),
        (
            "tools.slack.post_message",
            {"error": "channel_not_found"},
            "channel_not_found",
        ),
    ],
)
async def test_slack_stream_sink_formats_tool_failure(
    patched_slack_client: _FakeSlackClient,
    tool_name: str,
    tool_output: dict[str, str],
    details: str,
):
    sink = _sink("session-tool-failure")

    await sink.append(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-call-failure",
            tool_name=tool_name,
            tool_output=tool_output,
            is_error=True,
        )
    )
    await sink.done()

    task_chunks = _append_chunks(patched_slack_client, "task_update")
    assert len(task_chunks) == 1
    task_chunk = task_chunks[0]
    assert task_chunk["status"] == "error"
    assert task_chunk["details"] == details
    assert "output" not in task_chunk


@pytest.mark.anyio
async def test_slack_stream_sink_emits_approval_cards(
    patched_slack_client: _FakeSlackClient,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_redis = _FakeRedisClient()

    async def _get_redis() -> _FakeRedisClient:
        return fake_redis

    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.slack.get_redis_client",
        _get_redis,
    )
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "test-signing-secret")
    sink = _sink("session-8")

    await sink.append(
        _approval_request(
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
        )
    )
    await sink.done()

    post_messages = [
        payload
        for method, payload in patched_slack_client.calls
        if method == "chat.postMessage"
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
    assert batch["workspace_id"] == "workspace-session-8"
    assert sorted(batch["tool_call_ids"]) == [
        "tool-call-approve-1",
        "tool-call-approve-2",
    ]


@pytest.mark.anyio
async def test_slack_stream_sink_ignores_pending_approval_interrupt_errors(
    patched_slack_client: _FakeSlackClient,
):
    sink = _sink("session-approval-pending")

    await sink.append(
        _approval_request(
            ToolCallContent(
                id="tool-call-approval",
                name="core.http_request",
                input={"url": "https://example.com"},
            )
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

    statuses = [
        chunk.get("status")
        for chunk in _append_chunks(patched_slack_client, "task_update")
    ]
    assert "pending" in statuses
    assert "error" not in statuses
