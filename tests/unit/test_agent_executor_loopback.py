from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import TracebackType
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
from tracecat.artifacts.bindings import ArtifactSideEffect
from tracecat.artifacts.schemas import CaseArtifact
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus


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
    return LoopbackInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
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

    emitted = await handler.emit_terminal_error("runtime exited before connect")

    assert emitted is True
    assert handler.build_result().terminal_stream_error_emitted is True
    assert isinstance(handler._stream_sink, AgentStreamSink)
    stream_new.assert_awaited_once_with(
        session_id=loopback_input.session_id,
        workspace_id=loopback_input.workspace_id,
    )
    fake_stream.error.assert_awaited_once_with("runtime exited before connect")
    fake_stream.done.assert_awaited_once()


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
    fake_stream.done.assert_awaited_once()


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
    stream.done.assert_awaited_once()


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
    stream.done.assert_awaited_once()


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
    stream.done.assert_awaited_once()
