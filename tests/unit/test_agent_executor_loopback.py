from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import TracebackType
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import orjson
import pytest
from sqlalchemy.exc import SQLAlchemyError

import tracecat.agent.executor.loopback as loopback_module
from tracecat.agent.channels.sinks.slack import SlackStreamSink
from tracecat.agent.common.protocol import RuntimeEventEnvelope
from tracecat.agent.common.socket_io import MAX_PAYLOAD_SIZE, MessageType, build_message
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.executor.loopback import (
    AgentStreamSink,
    FanoutStreamSink,
    LoopbackHandler,
    LoopbackInput,
    RuntimeEnvelopeProtocolError,
    _runtime_envelope_from_json,
)
from tracecat.agent.stream.connector import AgentStream
from tracecat.artifacts.bindings import ArtifactSideEffect
from tracecat.artifacts.schemas import CaseArtifact
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus
from tracecat.db.models import AgentSessionHistory
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)


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


class _FakeSessionContext:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class _FakeArtifactPersistenceSession:
    def __init__(self, organization_id: UUID | None) -> None:
        self.scalar = AsyncMock(return_value=organization_id)


class _FakeHistoryPersistenceSession:
    def __init__(self) -> None:
        self.entries: list[AgentSessionHistory] = []
        self.commit = AsyncMock()

    def add(self, entry: object) -> None:
        assert isinstance(entry, AgentSessionHistory)
        self.entries.append(entry)


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
    del tmp_path
    workspace_id = uuid.uuid4()
    return LoopbackInput(
        session_id=uuid.uuid4(),
        workspace_id=workspace_id,
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
        stream_id=loopback_input.active_stream_id,
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

    emitted = await handler.emit_terminal_error("runtime exited before connect")

    assert emitted is True
    assert handler.build_result().terminal_stream_error_emitted is True
    assert isinstance(handler._stream_sink, AgentStreamSink)
    stream_new.assert_awaited_once_with(
        session_id=loopback_input.session_id,
        workspace_id=loopback_input.workspace_id,
        stream_id=loopback_input.active_stream_id,
    )
    fake_stream.error.assert_awaited_once_with("runtime exited before connect")
    fake_stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_emit_terminal_error_emits_failed_compaction_when_pending(
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

    handler._started_compaction_event = True

    emitted = await handler.emit_terminal_error("runtime exited before connect")

    assert emitted is True
    assert handler.build_result().terminal_stream_error_emitted is True
    fake_stream.append.assert_awaited_once()
    await_args = fake_stream.append.await_args
    assert await_args is not None
    failed_event = await_args.args[0]
    assert failed_event.type == StreamEventType.COMPACTION
    assert failed_event.metadata == {"phase": "failed"}
    fake_stream.error.assert_awaited_once_with("runtime exited before connect")
    fake_stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_emit_terminal_error_bounds_stalled_stream_sink(
    monkeypatch: pytest.MonkeyPatch, loopback_input: LoopbackInput
) -> None:
    async def stalled_error(_error: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        loopback_module,
        "TERMINAL_STREAM_ERROR_TIMEOUT_SECONDS",
        0.01,
    )
    handler = LoopbackHandler(input=loopback_input)
    fake_stream = _FakeStream()
    fake_stream.error.side_effect = stalled_error
    handler._stream_sink = fake_stream

    emitted = await handler.emit_terminal_error("provider request failed")

    assert emitted is False
    assert handler.build_result().terminal_stream_error_emitted is False
    fake_stream.error.assert_awaited_once_with("provider request failed")


@pytest.mark.anyio
async def test_emit_terminal_error_bounds_stalled_stream_sink_initialization(
    monkeypatch: pytest.MonkeyPatch, loopback_input: LoopbackInput
) -> None:
    async def stalled_initialization() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        loopback_module,
        "TERMINAL_STREAM_ERROR_TIMEOUT_SECONDS",
        0.01,
    )
    handler = LoopbackHandler(input=loopback_input)
    initialize_stream_sink = AsyncMock(side_effect=stalled_initialization)
    monkeypatch.setattr(handler, "_initialize_stream_sink", initialize_stream_sink)

    emitted = await handler.emit_terminal_error("provider request failed")

    assert emitted is False
    assert handler.build_result().terminal_stream_error_emitted is False
    initialize_stream_sink.assert_awaited_once()


@pytest.mark.anyio
async def test_prepare_initializes_stream_sink_once(
    monkeypatch: pytest.MonkeyPatch, loopback_input: LoopbackInput
) -> None:
    handler = LoopbackHandler(input=loopback_input)
    fake_stream = _FakeStream()
    initialize_stream_sink = AsyncMock(return_value=fake_stream)
    monkeypatch.setattr(handler, "_initialize_stream_sink", initialize_stream_sink)

    first = await handler.prepare()
    second = await handler.prepare()

    assert first is fake_stream
    assert second is fake_stream
    initialize_stream_sink.assert_awaited_once()


def _make_handler() -> LoopbackHandler:
    return LoopbackHandler(
        input=LoopbackInput(
            session_id=UUID("00000000-0000-0000-0000-000000000001"),
            workspace_id=UUID("00000000-0000-0000-0000-000000000002"),
        )
    )


@pytest.mark.anyio
async def test_persist_session_line_preserves_raw_nul_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _make_handler()
    handler._sdk_session_id = "sdk-session"
    session = _FakeHistoryPersistenceSession()
    monkeypatch.setattr(
        "tracecat.agent.executor.loopback.get_async_session_bypass_rls_context_manager",
        lambda: _FakeSessionContext(session),
    )
    raw_line = (
        r'{"type":"user","uuid":"line-uuid","message":{"role":"user",'
        r'"content":"left\u0000right"}}'
    )

    await handler._persist_session_line("sdk-session", raw_line)

    assert len(session.entries) == 1
    [entry] = session.entries
    assert entry.content["message"]["content"] == r"left\u0000right"
    assert entry.raw_session_line == raw_line.encode()
    assert entry.raw_session_line is not None
    assert orjson.loads(entry.raw_session_line)["message"]["content"] == (
        "left\x00right"
    )
    assert handler._persisted_line_uuids == {"line-uuid"}
    session.commit.assert_awaited_once()


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
async def test_interrupted_tool_call_ids_collected_from_cancel_state() -> None:
    """Interrupt casualties are derived from cancel-then-error ordering and
    unresolved calls - never from inspecting error text."""
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream

    def _tool_start(tool_call_id: str) -> UnifiedStreamEvent:
        return UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            tool_call_id=tool_call_id,
            tool_name="core.tables.list_tables",
            tool_input={},
        )

    # Genuine failure before the interrupt: not an interrupt casualty, even
    # though its error text looks abort-ish.
    await handler.send_stream_event(_tool_start("tool-failed"))
    await handler.send_stream_event(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-failed",
            tool_name="core.tables.list_tables",
            tool_output="The operation was aborted: table not found",
            is_error=True,
        )
    )

    # Two calls are in flight when the user interrupts.
    await handler.send_stream_event(_tool_start("tool-aborted"))
    await handler.send_stream_event(_tool_start("tool-unresolved"))
    handler.mark_cancelled("user_cancel")

    # SDK abort artifact lands after cancellation; the other call never
    # produces a result.
    await handler.send_stream_event(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="tool-aborted",
            tool_name="core.tables.list_tables",
            tool_output="request cancelled",
            is_error=True,
        )
    )

    await handler._emit_interrupt_notice_if_cancelled(stream)

    result = handler.build_result()
    assert result.interrupted_tool_call_ids == ["tool-aborted", "tool-unresolved"]

    cancelled_events = [
        call.args[0]
        for call in stream.append.await_args_list
        if call.args[0].type is StreamEventType.CANCELLED
    ]
    assert len(cancelled_events) == 1
    assert cancelled_events[0].metadata == {
        "reason": "user_cancel",
        "tool_call_ids": ["tool-aborted", "tool-unresolved"],
    }


def test_interrupted_tool_call_ids_empty_without_cancellation() -> None:
    handler = _make_handler()
    handler._tool_names_by_call_id["tool-open"] = "core.tables.list_tables"

    result = handler.build_result()

    assert result.interrupted_tool_call_ids == []


@pytest.mark.anyio
async def test_tool_result_emits_artifact_side_effect_from_tracked_call() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream

    async def persist_passthrough(
        effects: list[ArtifactSideEffect],
    ) -> list[ArtifactSideEffect]:
        return effects

    persist_artifact_side_effects = AsyncMock(side_effect=persist_passthrough)
    handler._persist_artifact_side_effects = persist_artifact_side_effects

    await handler.send_stream_event(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id="toolu_123",
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
        )
    )
    await handler.send_stream_event(
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="toolu_123",
            tool_output=[
                {
                    "type": "text",
                    "text": orjson.dumps(
                        {
                            "id": "case_123",
                            "summary": "Suspicious login",
                            "severity": "high",
                            "status": "new",
                        }
                    ).decode(),
                }
            ],
        )
    )

    append_calls = [call.args[0] for call in stream.append.await_args_list]
    assert [event.type for event in append_calls] == [
        StreamEventType.TOOL_CALL_STOP,
        StreamEventType.TOOL_RESULT,
        StreamEventType.ARTIFACT,
    ]
    artifact_event = append_calls[-1]
    assert artifact_event.artifact_data is not None
    assert artifact_event.artifact_data.op == "upsert"
    assert artifact_event.artifact_data.artifact == {
        "type": "case",
        "id": "case_123",
        "title": "Suspicious login",
        "scope": {"parentToolCallId": "toolu_123"},
        "severity": "high",
        "status": "new",
    }
    persist_artifact_side_effects.assert_awaited_once()
    persist_call = persist_artifact_side_effects.await_args
    assert persist_call is not None
    artifact_effects = persist_call.args[0]
    assert len(artifact_effects) == 1
    assert artifact_effects[0].op == "upsert"


@pytest.mark.anyio
async def test_persist_artifact_side_effects_uses_workspace_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _make_handler()
    organization_id = UUID("00000000-0000-0000-0000-000000000003")
    fake_session = _FakeArtifactPersistenceSession(organization_id)
    apply_artifact_side_effects = AsyncMock()
    captured_roles: list[Role] = []

    class FakeAgentSessionService:
        def __init__(self, session: object, role: Role) -> None:
            assert session is fake_session
            captured_roles.append(role)
            self.apply_artifact_side_effects = apply_artifact_side_effects

    monkeypatch.setattr(
        "tracecat.agent.executor.loopback.get_async_session_bypass_rls_context_manager",
        lambda: _FakeSessionContext(fake_session),
    )
    monkeypatch.setattr(
        "tracecat.agent.executor.loopback.AgentSessionService",
        FakeAgentSessionService,
    )

    effect = ArtifactSideEffect(
        op="upsert",
        artifact=CaseArtifact(
            id="case_123",
            title="Suspicious login",
            severity=CaseSeverity.HIGH,
            status=CaseStatus.NEW,
        ),
    )

    await handler._persist_artifact_side_effects([effect])

    fake_session.scalar.assert_awaited_once()
    assert len(captured_roles) == 1
    role = captured_roles[0]
    assert role.workspace_id == handler.input.workspace_id
    assert role.organization_id == organization_id
    apply_artifact_side_effects.assert_awaited_once_with(
        handler.input.session_id,
        [effect],
    )


@pytest.mark.anyio
async def test_close_external_stream_leaves_redis_open() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream

    await handler._close_external_stream()

    stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_terminal_success_closes_only_external_sink() -> None:
    handler = _make_handler()
    redis_stream = _FakeStream()
    external_stream = _FakeExternalSink()
    handler._stream_sink = FanoutStreamSink(
        sinks=(
            AgentStreamSink(stream=cast(AgentStream, redis_stream)),
            external_stream,
        )
    )

    await handler.send_result(output={"status": "done"})
    await handler.send_done()
    await handler.send_done()

    assert handler.build_result().success is True
    redis_stream.done.assert_not_awaited()
    external_stream.done.assert_awaited_once()
    assert handler._external_stream_done_emitted is True


@pytest.mark.anyio
async def test_terminal_error_streams_error_and_closes_only_external_sink() -> None:
    handler = _make_handler()
    redis_stream = _FakeStream()
    external_stream = _FakeExternalSink()
    handler._stream_sink = FanoutStreamSink(
        sinks=(
            AgentStreamSink(stream=cast(AgentStream, redis_stream)),
            external_stream,
        )
    )

    await handler.send_error("runtime failed")

    redis_stream.error.assert_awaited_once_with("runtime failed")
    external_stream.error.assert_awaited_once_with("runtime failed")
    redis_stream.done.assert_not_awaited()
    external_stream.done.assert_awaited_once()
    assert handler._external_stream_done_emitted is True
    assert handler._result.classification is not None
    assert handler._result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert (
        handler._result.classification.kind
        is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
    )
    assert (
        handler._result.classification.retry_disposition is RetryDisposition.RETRYABLE
    )


@pytest.mark.anyio
async def test_terminal_error_leaves_redis_open_for_workflow() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    event_order: list[str] = []

    async def record_error(error: str) -> None:
        assert error == "runtime failed"
        event_order.append("error")

    stream.error.side_effect = record_error
    handler._stream_sink = stream

    await handler.send_error("runtime failed")

    assert event_order == ["error"]
    stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_close_external_stream_is_deduplicated_on_approval_pause() -> None:
    handler = _make_handler()
    redis_stream = _FakeStream()
    external_stream = _FakeExternalSink()
    handler._stream_sink = FanoutStreamSink(
        sinks=(
            AgentStreamSink(stream=cast(AgentStream, redis_stream)),
            external_stream,
        )
    )
    handler._result.approval_requested = True

    await handler._close_external_stream()
    await handler._close_external_stream()

    redis_stream.done.assert_not_awaited()
    external_stream.done.assert_awaited_once()
    assert handler._external_stream_done_emitted is True


@pytest.mark.anyio
async def test_process_runtime_events_emits_failed_compaction_on_runtime_error() -> (
    None
):
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(
        RuntimeEventEnvelope.from_stream_event(
            UnifiedStreamEvent.compaction_event(phase="started")
        ),
        RuntimeEventEnvelope.from_stream_event(
            UnifiedStreamEvent(
                type=StreamEventType.ERROR,
                error="request_timeout: LLM gateway timed out",
                is_error=True,
            )
        ),
    )

    await handler._process_runtime_events(reader)

    append_calls = [call.args[0] for call in stream.append.await_args_list]
    assert [
        event.metadata
        for event in append_calls
        if event.type == StreamEventType.COMPACTION
    ] == [
        {"phase": "started"},
        {"phase": "failed"},
    ]
    stream.error.assert_awaited_once_with("request_timeout: LLM gateway timed out")
    stream.done.assert_not_awaited()
    assert handler._result.classification is not None
    assert handler._result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert (
        handler._result.classification.kind
        is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
    )
    assert (
        handler._result.classification.retry_disposition is RetryDisposition.RETRYABLE
    )
    assert handler._result.terminal_stream_error_emitted is True


@pytest.mark.anyio
async def test_process_runtime_events_emits_failed_compaction_on_done_without_boundary() -> (
    None
):
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(
        RuntimeEventEnvelope.from_stream_event(
            UnifiedStreamEvent.compaction_event(phase="started")
        ),
        RuntimeEventEnvelope.from_result(
            usage={"requests": 0},
            output="Command rejected",
        ),
        RuntimeEventEnvelope.done(),
    )

    await handler._process_runtime_events(reader)

    append_calls = [call.args[0] for call in stream.append.await_args_list]
    assert [
        event.metadata
        for event in append_calls
        if event.type == StreamEventType.COMPACTION
    ] == [
        {"phase": "started"},
        {"phase": "failed"},
    ]
    stream.error.assert_not_awaited()
    stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_process_runtime_events_fails_when_done_arrives_without_result() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(RuntimeEventEnvelope.done())

    await handler._process_runtime_events(reader)

    assert handler._result.error == "Runtime completed without final result"
    assert handler._result.classification is not None
    assert (
        handler._result.classification.kind
        is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    )
    stream.error.assert_awaited_once_with("Runtime completed without final result")
    assert handler._result.terminal_stream_error_emitted is True
    stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_process_runtime_events_preserves_counts_on_limit_error() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(
        RuntimeEventEnvelope.from_result(
            num_turns=2,
            consumed_tool_calls=3,
        ),
        RuntimeEventEnvelope.from_error("Agent max_tool_calls exceeded (2)"),
    )

    await handler._process_runtime_events(reader)

    result = handler.build_result()
    assert result.success is False
    assert result.error == "Agent max_tool_calls exceeded (2)"
    assert result.result_num_turns == 2
    assert result.consumed_tool_calls == 3


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
    assert handler._result.classification is not None
    assert (
        handler._result.classification.kind
        is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    )
    stream.error.assert_awaited_once_with(
        "Runtime completed without assistant output or model usage"
    )
    assert handler._result.terminal_stream_error_emitted is True


@pytest.mark.anyio
async def test_process_runtime_events_classifies_disconnect_and_marks_streamed() -> (
    None
):
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream

    await handler._process_runtime_events(_reader_for_envelopes())

    assert handler._result.classification is not None
    assert handler._result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert (
        handler._result.classification.kind
        is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
    )
    assert handler._result.terminal_stream_error_emitted is True
    stream.error.assert_awaited_once_with("Runtime disconnected during execution")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        orjson.dumps({"event": {"type": StreamEventType.TEXT_DELTA.value}}),
        orjson.dumps({"type": "unknown"}),
        orjson.dumps({"type": "stream_event", "event": []}),
        orjson.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": StreamEventType.APPROVAL_REQUEST.value,
                    "approval_items": [1],
                },
            }
        ),
        orjson.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": StreamEventType.ARTIFACT.value,
                    "artifact_data": [],
                },
            }
        ),
    ],
)
async def test_handle_connection_classifies_invalid_runtime_envelope_as_protocol_failure(
    payload: bytes,
) -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = asyncio.StreamReader()
    reader.feed_data(build_message(MessageType.EVENT, payload))
    reader.feed_eof()
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    result = await handler.handle_connection(
        reader,
        cast(asyncio.StreamWriter, writer),
    )

    assert result.error == "Runtime sent an invalid event envelope"
    assert result.classification is not None
    assert result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    assert result.classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    stream.error.assert_awaited_once_with("Runtime sent an invalid event envelope")
    assert result.terminal_stream_error_emitted is True
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "frame",
    [
        build_message(MessageType.INIT, b"{}"),
        bytes([0xFF]) + (0).to_bytes(4, "big"),
        bytes([MessageType.EVENT]) + (MAX_PAYLOAD_SIZE + 1).to_bytes(4, "big"),
    ],
)
async def test_handle_connection_classifies_invalid_runtime_frame_as_protocol_failure(
    frame: bytes,
) -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    result = await handler.handle_connection(
        reader,
        cast(asyncio.StreamWriter, writer),
    )

    assert result.error == "Runtime sent an invalid event envelope"
    assert result.classification is not None
    assert result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    assert result.classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    stream.error.assert_awaited_once_with("Runtime sent an invalid event envelope")
    assert result.terminal_stream_error_emitted is True
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_connection_bounds_protocol_error_stream_emission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stalled_error(_error: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        loopback_module,
        "TERMINAL_STREAM_ERROR_TIMEOUT_SECONDS",
        0.01,
    )
    handler = _make_handler()
    stream = _FakeStream()
    stream.error.side_effect = stalled_error
    handler._stream_sink = stream
    reader = asyncio.StreamReader()
    reader.feed_data(build_message(MessageType.EVENT, b"{"))
    reader.feed_eof()
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    result = await handler.handle_connection(
        reader,
        cast(asyncio.StreamWriter, writer),
    )

    assert result.error == "Runtime sent an invalid event envelope"
    assert result.classification is not None
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    assert result.terminal_stream_error_emitted is False
    stream.error.assert_awaited_once_with("Runtime sent an invalid event envelope")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_connection_preserves_protocol_error_when_stream_sink_fails() -> (
    None
):
    handler = _make_handler()
    stream = _FakeStream()
    stream.error.side_effect = ConnectionError("stream unavailable")
    handler._stream_sink = stream
    reader = asyncio.StreamReader()
    reader.feed_data(build_message(MessageType.EVENT, b"{"))
    reader.feed_eof()
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    result = await handler.handle_connection(
        reader,
        cast(asyncio.StreamWriter, writer),
    )

    assert result.error == "Runtime sent an invalid event envelope"
    assert result.classification is not None
    assert result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    assert result.classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert result.terminal_stream_error_emitted is False
    stream.error.assert_awaited_once_with("Runtime sent an invalid event envelope")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_connection_deadline_cancels_slack_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stalled_operation(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        loopback_module,
        "TERMINAL_STREAM_ERROR_TIMEOUT_SECONDS",
        0.01,
    )
    slack_sink = SlackStreamSink(
        slack_bot_token="xoxb-test",
        channel_id="C123",
        thread_ts="1700000000.000001",
        reaction_ts="1700000000.000001",
        session_id="session-1",
        workspace_id="workspace-1",
    )
    append_stream_text = AsyncMock(side_effect=stalled_operation)
    terminal_reaction = AsyncMock(side_effect=stalled_operation)
    monkeypatch.setattr(slack_sink, "_append_stream_text", append_stream_text)
    monkeypatch.setattr(slack_sink, "_set_terminal_reaction", terminal_reaction)

    handler = _make_handler()
    handler._stream_sink = slack_sink
    reader = asyncio.StreamReader()
    reader.feed_data(build_message(MessageType.EVENT, b"{"))
    reader.feed_eof()
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    result = await asyncio.wait_for(
        handler.handle_connection(
            reader,
            cast(asyncio.StreamWriter, writer),
        ),
        timeout=0.2,
    )

    assert result.classification is not None
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    assert result.terminal_stream_error_emitted is False
    append_stream_text.assert_awaited_once()
    terminal_reaction.assert_not_awaited()
    assert slack_sink._is_closed is True


@pytest.mark.parametrize(
    "envelope",
    [
        {
            "type": "stream_event",
            "event": {"type": "approval_request"},
        },
        {
            "type": "stream_event",
            "event": {"type": "approval_request", "approval_items": []},
        },
        {
            "type": "stream_event",
            "event": {
                "type": "approval_request",
                "approval_items": [{"id": [], "name": "tool"}],
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "approval_request",
                "approval_items": [{"id": "call", "name": "tool", "input": []}],
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "approval_request",
                "approval_items": [{"id": "call", "name": "tool", "metadata": []}],
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "approval_request",
                "approval_items": [{"id": "call", "name": "tool", "status": "waiting"}],
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "approval_request",
                "approval_items": [{"id": "call", "name": "tool", "decision": []}],
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "approval_request",
                "approval_items": [
                    {
                        "id": "call",
                        "name": "tool",
                        "decision": {"value": "yes", "metadata": {}},
                    }
                ],
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "artifact",
                "artifact_data": {"op": "replace", "artifact": {}},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "artifact",
                "artifact_data": {"op": "upsert", "artifact": []},
            },
        },
        {"type": "stream_event", "event": {"type": "text_delta", "part_id": True}},
        {"type": "stream_event", "event": {"type": "text_delta", "text": []}},
        {
            "type": "stream_event",
            "event": {"type": "tool_call_start", "tool_call_id": []},
        },
        {
            "type": "stream_event",
            "event": {"type": "tool_call_start", "tool_input": []},
        },
        {
            "type": "stream_event",
            "event": {"type": "tool_result", "is_error": "false"},
        },
        {
            "type": "stream_event",
            "event": {"type": "compaction", "metadata": []},
        },
        {"type": "stream_event", "event": {"type": "text_delta", "timestamp": 1}},
        {"type": "message", "message": []},
        {"type": "message"},
        {"type": "session_line", "session_line": 1, "sdk_session_id": "sdk"},
        {"type": "session_line", "session_line": "{}", "sdk_session_id": ""},
        {"type": "session_line", "session_line": "{}", "internal": 1},
        {"type": "session_update", "sdk_session_id": "", "sdk_session_data": "{}"},
        {"type": "session_update", "sdk_session_id": "sdk"},
        {"type": "error"},
        {"type": "result", "result_usage": []},
        {"type": "result", "result_num_turns": True},
        {"type": "result", "result_duration_ms": "1"},
        {"type": "log", "log_level": "fatal", "log_message": "message"},
        {"type": "log", "log_level": "info", "log_message": []},
        {
            "type": "log",
            "log_level": "info",
            "log_message": "message",
            "log_extra": [],
        },
        {
            "type": "log",
            "log_level": "info",
            "log_message": "message",
            "log_extra": {"session_id": "spoofed"},
        },
        {
            "type": "log",
            "log_level": "info",
            "log_message": "message",
            "log_extra": {"self": "spoofed"},
        },
        {
            "type": "log",
            "log_level": "info",
            "log_message": "message",
            "log_extra": {"level": "spoofed"},
        },
        {
            "type": "log",
            "log_level": "info",
            "log_message": "message",
            "log_extra": {"message": "spoofed"},
        },
    ],
)
def test_runtime_envelope_parser_rejects_malformed_typed_fields(
    envelope: dict[str, object],
) -> None:
    with pytest.raises(RuntimeEnvelopeProtocolError):
        _runtime_envelope_from_json(orjson.dumps(envelope))


@pytest.mark.anyio
@pytest.mark.parametrize("session_line", ["", "{", "[]"])
async def test_handle_connection_classifies_malformed_session_line_as_protocol_failure(
    session_line: str,
) -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    reader = _reader_for_envelopes(
        RuntimeEventEnvelope.from_session_line(
            "sdk-session",
            session_line,
        )
    )
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    result = await handler.handle_connection(
        reader,
        cast(asyncio.StreamWriter, writer),
    )

    assert result.error == "Runtime sent an invalid event envelope"
    assert result.classification is not None
    assert result.classification.owner is RuntimeErrorOwner.PLATFORM
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED
    assert result.classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    stream.error.assert_awaited_once_with("Runtime sent an invalid event envelope")
    assert result.terminal_stream_error_emitted is True
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.anyio
async def test_send_done_preserves_existing_error_state() -> None:
    handler = _make_handler()
    stream = _FakeStream()
    handler._stream_sink = stream
    handler._result.error = "runtime failed"

    await handler.send_done()

    assert handler._result.success is False
    assert handler._result.error == "runtime failed"
    stream.error.assert_not_awaited()
    stream.done.assert_not_awaited()


@pytest.mark.anyio
async def test_parallel_approval_requests_accumulate_across_events() -> None:
    """N parallel gated tool calls arrive as N approval events; keep them all."""
    handler = _make_handler()
    handler._stream_sink = _FakeStream()

    first = UnifiedStreamEvent.approval_request_event(
        [ToolCallContent(id="call-1", name="core.http_request", input={"n": 1})]
    )
    second = UnifiedStreamEvent.approval_request_event(
        [ToolCallContent(id="call-2", name="core.http_request", input={"n": 2})]
    )

    await handler._handle_stream_event(first)
    await handler._handle_stream_event(second)

    result = handler.build_result()
    assert result.approval_requested is True
    assert [item.id for item in result.approval_items] == ["call-1", "call-2"]
    assert handler._pending_approval_tool_call_ids == {"call-1", "call-2"}


@pytest.mark.anyio
async def test_accumulated_approval_request_copies_input() -> None:
    handler = _make_handler()
    handler._stream_sink = _FakeStream()
    original_input = {"n": 1, "nested": {"flag": True}}

    event = UnifiedStreamEvent.approval_request_event(
        [
            ToolCallContent(
                id="call-1",
                name="core.http_request",
                input=original_input,
            )
        ]
    )

    await handler._handle_stream_event(event)

    original_input["n"] = 2
    original_input["nested"]["flag"] = False

    result = handler.build_result()
    assert result.approval_items[0].input == {"n": 1, "nested": {"flag": True}}


@pytest.mark.anyio
async def test_duplicate_approval_request_events_are_deduped() -> None:
    handler = _make_handler()
    handler._stream_sink = _FakeStream()

    event = UnifiedStreamEvent.approval_request_event(
        [ToolCallContent(id="call-1", name="core.http_request", input={"n": 1})]
    )

    await handler._handle_stream_event(event)
    await handler._handle_stream_event(event)

    result = handler.build_result()
    assert [item.id for item in result.approval_items] == ["call-1"]
