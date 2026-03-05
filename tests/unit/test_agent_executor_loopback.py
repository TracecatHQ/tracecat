from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tracecat.agent.executor.loopback import (
    AgentStreamSink,
    LoopbackHandler,
    LoopbackInput,
)
from tracecat.agent.types import AgentConfig


class _FakeStream:
    def __init__(self) -> None:
        self.append = AsyncMock()
        self.error = AsyncMock()
        self.done = AsyncMock()


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
        litellm_auth_token="litellm-token",
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
