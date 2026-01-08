"""Unit tests for agent runtime socket communication.

Tests the socket protocol between orchestrator and runtime:
- Payload serialization/deserialization
- Event streaming
- Error handling

Uses real Unix sockets but mocks the Claude SDK.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.sandbox.protocol import (
    RuntimeEventEnvelope,
    RuntimeEventEnvelopeTA,
    RuntimeInitPayload,
)
from tracecat.agent.sandbox.runtime import ClaudeAgentRuntime
from tracecat.agent.sandbox.socket_io import SocketStreamWriter
from tracecat.agent.stream.types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.types import AgentConfig


def make_init_payload(
    mcp_socket_path: str,
) -> RuntimeInitPayload:
    """Create a minimal init payload for testing."""
    return RuntimeInitPayload(
        session_id=uuid.uuid4(),
        mcp_socket_path=mcp_socket_path,
        jwt_token="test-jwt-token",
        config=AgentConfig(
            model_name="claude-3-5-sonnet-20241022",
            model_provider="anthropic",
            instructions="You are a test assistant.",
        ),
        user_prompt="Say hello",
        litellm_base_url="http://localhost:8080",
        litellm_auth_token="test-litellm-token",
        allowed_actions={},
    )


class TestRuntimeSocketCommunication:
    """Unit tests for runtime socket communication."""

    @pytest.fixture
    def mock_claude_sdk_client(self) -> MagicMock:
        """Create a mock ClaudeSDKClient."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.query = AsyncMock()
        mock_client.interrupt = AsyncMock()

        # Return empty response (no streaming events)
        async def empty_receive() -> Any:
            return
            yield  # noqa: B901

        mock_client.receive_response = empty_receive
        return mock_client

    @pytest.mark.anyio
    async def test_runtime_streams_events_over_socket(
        self,
        mock_claude_sdk_client: MagicMock,
    ) -> None:
        """Test that runtime streams events correctly over Unix socket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            # Collected events
            received_events: list[RuntimeEventEnvelope] = []
            runtime_done = asyncio.Event()

            async def run_server() -> asyncio.Server:
                """Run socket server and collect events."""

                async def handle_client(
                    reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter,
                ) -> None:
                    try:
                        while True:
                            line = await reader.readline()
                            if not line:
                                break
                            envelope = RuntimeEventEnvelopeTA.validate_json(line)
                            received_events.append(envelope)
                            if envelope.type == "done":
                                runtime_done.set()
                                break
                    finally:
                        writer.close()
                        await writer.wait_closed()

                server = await asyncio.start_unix_server(
                    handle_client,
                    path=str(socket_path),
                )
                return server

            # Start server
            server = await run_server()

            try:
                # Connect runtime to socket
                _, writer = await asyncio.open_unix_connection(str(socket_path))
                socket_writer = SocketStreamWriter(writer)

                # Create and run runtime with mocked SDK
                with (
                    patch(
                        "tracecat.agent.runtime.ClaudeSDKClient",
                        return_value=mock_claude_sdk_client,
                    ),
                    patch(
                        "tracecat.agent.runtime.create_proxy_mcp_server",
                        AsyncMock(return_value={}),
                    ),
                ):
                    runtime = ClaudeAgentRuntime(socket_writer)
                    payload = make_init_payload(mcp_socket_path="/tmp/mcp.sock")
                    await runtime.run(payload)

                # Wait for done event
                await asyncio.wait_for(runtime_done.wait(), timeout=5.0)

                # Verify events
                assert len(received_events) >= 2  # user_message + done

                # First should be user message
                first = received_events[0]
                assert first.type == "event"
                assert first.event is not None
                assert first.event.type == StreamEventType.USER_MESSAGE
                assert first.event.text == "Say hello"

                # Last should be done
                last = received_events[-1]
                assert last.type == "done"

            finally:
                server.close()
                await server.wait_closed()

    @pytest.mark.anyio
    async def test_runtime_streams_sdk_events(
        self,
        mock_claude_sdk_client: MagicMock,
    ) -> None:
        """Test that SDK events are streamed over socket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[RuntimeEventEnvelope] = []
            runtime_done = asyncio.Event()

            # Mock SDK to yield a stream event
            mock_stream_event = MagicMock()
            mock_stream_event.event = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello!"},
            }

            async def mock_receive() -> Any:
                yield mock_stream_event

            mock_claude_sdk_client.receive_response = mock_receive

            # Mock adapter
            mock_adapter = MagicMock()
            mock_adapter.to_unified_event.return_value = UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                text="Hello!",
                part_id=0,
            )

            async def handle_client(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                try:
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        envelope = RuntimeEventEnvelopeTA.validate_json(line)
                        received_events.append(envelope)
                        if envelope.type == "done":
                            runtime_done.set()
                            break
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_unix_server(
                handle_client,
                path=str(socket_path),
            )

            try:
                reader, writer = await asyncio.open_unix_connection(str(socket_path))
                socket_writer = SocketStreamWriter(writer)

                with (
                    patch(
                        "tracecat.agent.runtime.ClaudeSDKClient",
                        return_value=mock_claude_sdk_client,
                    ),
                    patch(
                        "tracecat.agent.runtime.create_proxy_mcp_server",
                        AsyncMock(return_value={}),
                    ),
                    patch(
                        "tracecat.agent.runtime.ClaudeSDKAdapter",
                        return_value=mock_adapter,
                    ),
                    patch("tracecat.agent.runtime.StreamEvent", MagicMock),
                ):
                    runtime = ClaudeAgentRuntime(socket_writer)
                    payload = make_init_payload(mcp_socket_path="/tmp/mcp.sock")
                    await runtime.run(payload)

                await asyncio.wait_for(runtime_done.wait(), timeout=5.0)

                # Should have user_message, text_delta, done
                event_types = [
                    (e.type, e.event.type if e.event else None) for e in received_events
                ]
                assert ("event", StreamEventType.USER_MESSAGE) in event_types
                assert ("event", StreamEventType.TEXT_DELTA) in event_types
                assert ("done", None) in event_types

            finally:
                server.close()
                await server.wait_closed()

    @pytest.mark.anyio
    async def test_runtime_sends_error_on_exception(
        self,
        mock_claude_sdk_client: MagicMock,
    ) -> None:
        """Test that runtime sends error event on SDK exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[RuntimeEventEnvelope] = []
            runtime_done = asyncio.Event()

            # Make SDK raise an error
            mock_claude_sdk_client.query = AsyncMock(
                side_effect=ValueError("SDK connection failed")
            )

            async def handle_client(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                try:
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        envelope = RuntimeEventEnvelopeTA.validate_json(line)
                        received_events.append(envelope)
                        if envelope.type == "done":
                            runtime_done.set()
                            break
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_unix_server(
                handle_client,
                path=str(socket_path),
            )

            try:
                _, writer = await asyncio.open_unix_connection(str(socket_path))
                socket_writer = SocketStreamWriter(writer)

                with (
                    patch(
                        "tracecat.agent.runtime.ClaudeSDKClient",
                        return_value=mock_claude_sdk_client,
                    ),
                    patch(
                        "tracecat.agent.runtime.create_proxy_mcp_server",
                        AsyncMock(return_value={}),
                    ),
                    pytest.raises(ValueError, match="SDK connection failed"),
                ):
                    runtime = ClaudeAgentRuntime(socket_writer)
                    payload = make_init_payload(mcp_socket_path="/tmp/mcp.sock")
                    await runtime.run(payload)

                await asyncio.wait_for(runtime_done.wait(), timeout=5.0)

                # Should have error and done events
                event_types = [e.type for e in received_events]
                assert "error" in event_types
                assert "done" in event_types

                # Verify error message
                error_event = next(e for e in received_events if e.type == "error")
                assert "SDK connection failed" in (error_event.error or "")

            finally:
                server.close()
                await server.wait_closed()

    @pytest.mark.anyio
    async def test_session_update_streamed_over_socket(
        self,
    ) -> None:
        """Test that session updates are correctly streamed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[RuntimeEventEnvelope] = []

            async def handle_client(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                try:
                    while True:
                        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                        if not line:
                            break
                        envelope = RuntimeEventEnvelopeTA.validate_json(line)
                        received_events.append(envelope)
                except TimeoutError:
                    pass
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_unix_server(
                handle_client,
                path=str(socket_path),
            )

            try:
                _, writer = await asyncio.open_unix_connection(str(socket_path))
                socket_writer = SocketStreamWriter(writer)

                # Send session update directly
                await socket_writer.send_session_update(
                    sdk_session_id="test-session-123",
                    sdk_session_data='{"message": "hello"}\n',
                )
                await socket_writer.send_done()
                await socket_writer.close()

                # Give server time to receive
                await asyncio.sleep(0.1)

                # Verify session update was received
                session_updates = [
                    e for e in received_events if e.type == "session_update"
                ]
                assert len(session_updates) == 1
                assert session_updates[0].sdk_session_id == "test-session-123"
                assert session_updates[0].sdk_session_data == '{"message": "hello"}\n'

            finally:
                server.close()
                await server.wait_closed()
