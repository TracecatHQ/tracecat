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

import orjson
import pytest

from tracecat.agent.common.protocol import RuntimeEventEnvelope, RuntimeInitPayload
from tracecat.agent.common.socket_io import SocketStreamWriter, read_message
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime


def make_init_payload() -> RuntimeInitPayload:
    """Create a minimal init payload for testing."""
    return RuntimeInitPayload(
        session_id=uuid.uuid4(),
        mcp_auth_token="test-jwt-token",
        config=SandboxAgentConfig(
            model_name="claude-3-5-sonnet-20241022",
            model_provider="anthropic",
            instructions="You are a test assistant.",
        ),
        user_prompt="Say hello",
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
        """Test that runtime streams done event correctly over Unix socket."""
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
                            try:
                                _, payload = await read_message(reader)
                            except asyncio.IncompleteReadError:
                                break
                            envelope = RuntimeEventEnvelope.from_dict(
                                orjson.loads(payload)
                            )
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
                        "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                        return_value=mock_claude_sdk_client,
                    ),
                    patch(
                        "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                        AsyncMock(return_value={}),
                    ),
                ):
                    runtime = ClaudeAgentRuntime(socket_writer)
                    payload = make_init_payload()
                    await runtime.run(payload)

                # Wait for done event
                await asyncio.wait_for(runtime_done.wait(), timeout=5.0)

                # Verify done event was received
                # With empty receive_response, runtime sends only "done"
                assert len(received_events) >= 1
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
            mock_stream_event.session_id = "test-session"
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
                        try:
                            _, payload = await read_message(reader)
                        except asyncio.IncompleteReadError:
                            break
                        envelope = RuntimeEventEnvelope.from_dict(orjson.loads(payload))
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
                        "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                        return_value=mock_claude_sdk_client,
                    ),
                    patch(
                        "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                        AsyncMock(return_value={}),
                    ),
                    patch(
                        "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKAdapter",
                        return_value=mock_adapter,
                    ),
                    patch(
                        "tracecat.agent.runtime.claude_code.runtime.StreamEvent",
                        MagicMock,
                    ),
                ):
                    runtime = ClaudeAgentRuntime(socket_writer)
                    payload = make_init_payload()
                    await runtime.run(payload)

                await asyncio.wait_for(runtime_done.wait(), timeout=5.0)

                # Should have stream_event (text_delta) and done
                event_types = [
                    (e.type, e.event.type if e.event else None) for e in received_events
                ]
                assert ("stream_event", StreamEventType.TEXT_DELTA) in event_types
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
                        try:
                            _, payload = await read_message(reader)
                        except asyncio.IncompleteReadError:
                            break
                        envelope = RuntimeEventEnvelope.from_dict(orjson.loads(payload))
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
                        "tracecat.agent.runtime.claude_code.runtime.ClaudeSDKClient",
                        return_value=mock_claude_sdk_client,
                    ),
                    patch(
                        "tracecat.agent.runtime.claude_code.runtime.create_proxy_mcp_server",
                        AsyncMock(return_value={}),
                    ),
                    pytest.raises(ValueError, match="SDK connection failed"),
                ):
                    runtime = ClaudeAgentRuntime(socket_writer)
                    payload = make_init_payload()
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
                        try:
                            _, payload = await asyncio.wait_for(
                                read_message(reader), timeout=5.0
                            )
                        except asyncio.IncompleteReadError:
                            break
                        envelope = RuntimeEventEnvelope.from_dict(orjson.loads(payload))
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

    @pytest.mark.anyio
    async def test_log_event_streamed_over_socket(
        self,
    ) -> None:
        """Test that log events are correctly streamed via socket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[RuntimeEventEnvelope] = []

            async def handle_client(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                try:
                    while True:
                        try:
                            _, payload = await asyncio.wait_for(
                                read_message(reader), timeout=5.0
                            )
                        except asyncio.IncompleteReadError:
                            break
                        envelope = RuntimeEventEnvelope.from_dict(orjson.loads(payload))
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

                # Send log events with different levels
                await socket_writer.send_log(
                    "info", "Runtime initialized", model="gpt-4"
                )
                await socket_writer.send_log("debug", "Query sent", prompt_length=42)
                await socket_writer.send_log("warning", "Slow response detected")
                await socket_writer.send_log(
                    "error", "Connection failed", error_code=500
                )
                await socket_writer.send_done()
                await socket_writer.close()

                # Give server time to receive
                await asyncio.sleep(0.1)

                # Verify log events were received
                log_events = [e for e in received_events if e.type == "log"]
                assert len(log_events) == 4

                # Check first log event
                assert log_events[0].log_level == "info"
                assert log_events[0].log_message == "Runtime initialized"
                assert log_events[0].log_extra == {"model": "gpt-4"}

                # Check debug log
                assert log_events[1].log_level == "debug"
                assert log_events[1].log_message == "Query sent"
                assert log_events[1].log_extra == {"prompt_length": 42}

                # Check warning log
                assert log_events[2].log_level == "warning"
                assert log_events[2].log_message == "Slow response detected"
                assert log_events[2].log_extra is None

                # Check error log
                assert log_events[3].log_level == "error"
                assert log_events[3].log_message == "Connection failed"
                assert log_events[3].log_extra == {"error_code": 500}

            finally:
                server.close()
                await server.wait_closed()
