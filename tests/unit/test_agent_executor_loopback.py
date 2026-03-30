from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import orjson
import pytest
from sqlalchemy.exc import SQLAlchemyError

from tracecat.agent.common.protocol import RuntimeEventEnvelope
from tracecat.agent.common.socket_io import MessageType, build_message
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.executor.loopback import (
    AgentStreamSink,
    FanoutStreamSink,
    LoopbackHandler,
    LoopbackInput,
)
from tracecat.agent.types import AgentConfig


class _FakeStream:
    def __init__(self) -> None:
        self.append = AsyncMock()
        self.error = AsyncMock()
        self.done = AsyncMock()


class _FakeExternalSink:
    def __init__(self) -> None:
        self.append = AsyncMock()
        self.error = AsyncMock()
        self.done = AsyncMock()


def _reader_for_envelopes(*envelopes: RuntimeEventEnvelope) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for envelope in envelopes:
        reader.feed_data(
            build_message(
                MessageType.EVENT,
                orjson.dumps(envelope.to_dict()),
            )
        )
    reader.feed_eof()
    return reader


@pytest.fixture
def loopback_input(tmp_path: Path) -> LoopbackInput:
    return LoopbackInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_prompt="Investigate runtime crash",
        config=AgentConfig(
            model_name="claude-3-5-sonnet-20241022",
            model_provider="anthropic",
        ),
        mcp_auth_token="mcp-token",
        llm_gateway_auth_token="llm-token",
        socket_dir=tmp_path,
    )


@pytest.mark.anyio
async def test_initialize_stream_sink_falls_back_to_redis_on_external_lookup_error(
    monkeypatch: pytest.MonkeyPatch, loopback_input: LoopbackInput
) -> None:
    handler = LoopbackHandler(input=loopback_input)
    external_lookup = AsyncMock(side_effect=SQLAlchemyError("database unavailable"))
    monkeypatch.setattr(handler, "_build_external_channel_sink", external_lookup)

    fake_stream = _FakeStream()
    stream_new = AsyncMock(return_value=fake_stream)
    monkeypatch.setattr("tracecat.agent.executor.loopback.AgentStream.new", stream_new)

    sink = await handler._initialize_stream_sink()

    assert isinstance(sink, AgentStreamSink)
    assert sink.stream is fake_stream
    external_lookup.assert_awaited_once()
    stream_new.assert_awaited_once_with(
        session_id=loopback_input.session_id,
        workspace_id=loopback_input.workspace_id,
    )


@pytest.mark.anyio
async def test_initialize_stream_sink_uses_fanout_when_external_sink_available(
    monkeypatch: pytest.MonkeyPatch, loopback_input: LoopbackInput
) -> None:
    handler = LoopbackHandler(input=loopback_input)
    external_sink = _FakeExternalSink()
    monkeypatch.setattr(
        handler,
        "_build_external_channel_sink",
        AsyncMock(return_value=external_sink),
    )
    fake_stream = _FakeStream()
    stream_new = AsyncMock(return_value=fake_stream)
    monkeypatch.setattr("tracecat.agent.executor.loopback.AgentStream.new", stream_new)

    sink = await handler._initialize_stream_sink()
    event = UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, text="hello")
    await sink.append(event)

    assert isinstance(sink, FanoutStreamSink)
    fake_stream.append.assert_awaited_once_with(event)
    external_sink.append.assert_awaited_once_with(event)


@pytest.mark.anyio
async def test_emit_terminal_error_uses_redis_when_external_lookup_errors(
    monkeypatch: pytest.MonkeyPatch, loopback_input: LoopbackInput
) -> None:
    handler = LoopbackHandler(input=loopback_input)
    monkeypatch.setattr(
        handler,
        "_build_external_channel_sink",
        AsyncMock(side_effect=SQLAlchemyError("database unavailable")),
    )

    fake_stream = _FakeStream()
    stream_new = AsyncMock(return_value=fake_stream)
    monkeypatch.setattr("tracecat.agent.executor.loopback.AgentStream.new", stream_new)

    await handler.emit_terminal_error("runtime exited before connect")

    assert isinstance(handler._stream_sink, AgentStreamSink)
    stream_new.assert_awaited_once_with(
        session_id=loopback_input.session_id,
        workspace_id=loopback_input.workspace_id,
    )
    fake_stream.error.assert_awaited_once_with("runtime exited before connect")
    fake_stream.done.assert_awaited_once()


def _make_handler() -> LoopbackHandler:
    return LoopbackHandler(
        input=LoopbackInput(
            session_id=UUID("00000000-0000-0000-0000-000000000001"),
            workspace_id=UUID("00000000-0000-0000-0000-000000000002"),
            user_prompt="hi",
            config=AgentConfig(
                model_name="claude-3-7-sonnet",
                model_provider="anthropic",
            ),
            mcp_auth_token="mcp-token",
            llm_gateway_auth_token="llm-token",
            socket_dir=Path("/tmp"),
        )
    )


def test_should_suppress_pending_approval_tool_result() -> None:
    handler = _make_handler()
    handler._pending_approval_tool_call_ids.add("tool-1")

    event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        tool_call_id="tool-1",
        tool_name="core.cases.create_case",
        tool_output={"id": "case-123"},
    )

    assert handler._should_suppress_stream_event(event) is True


def test_should_suppress_synthetic_interrupt_output() -> None:
    handler = _make_handler()

    event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        tool_call_id="tool-2",
        tool_name="core.cases.create_case",
        tool_output="The user doesn't want to take this action right now.",
        is_error=True,
    )

    assert handler._should_suppress_stream_event(event) is True


def test_should_suppress_nested_interrupt_output() -> None:
    handler = _make_handler()

    event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        tool_call_id="tool-3",
        tool_name="core.cases.create_case",
        tool_output=[
            {
                "type": "text",
                "text": (
                    "STOP what you are doing and wait for the user to tell you"
                    " how to proceed."
                ),
            }
        ],
        is_error=True,
    )

    assert handler._should_suppress_stream_event(event) is True


def test_should_not_suppress_normal_tool_result_error() -> None:
    handler = _make_handler()

    event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        tool_call_id="tool-4",
        tool_name="core.cases.create_case",
        tool_output="Tool execution failed: timeout",
        is_error=True,
    )

    assert handler._should_suppress_stream_event(event) is False


@pytest.mark.anyio
async def test_process_runtime_events_fails_when_done_arrives_without_result() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(RuntimeEventEnvelope.done())

    await handler._process_runtime_events(reader)

    assert handler._result.error == "Runtime completed without final result"
    stream.error.assert_awaited_once_with("Runtime completed without final result")
    stream.done.assert_awaited_once()


@pytest.mark.anyio
async def test_process_runtime_events_fails_zero_work_completion() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(
        RuntimeEventEnvelope.from_result(
            usage={
                "requests": 0,
                "tool_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            output=None,
        ),
        RuntimeEventEnvelope.done(),
    )

    await handler._process_runtime_events(reader)

    assert (
        handler._result.error
        == "Runtime completed without assistant output or model usage"
    )
    stream.error.assert_awaited_once_with(
        "Runtime completed without assistant output or model usage"
    )
