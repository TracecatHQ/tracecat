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
from typing import Any, cast
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
        llm_gateway_auth_token="test-llm-token",
        allowed_actions={},
    )


class TestRuntimeSocketCommunication:
    """Unit tests for runtime socket communication."""

    @pytest.fixture(autouse=True)
    def _mock_llm_bridge_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set the LLM bridge port env var so get_litellm_url() succeeds."""
        monkeypatch.setenv("TRACECAT__LLM_BRIDGE_PORT", "12345")

    @pytest.fixture
    def mock_claude_sdk_client(self) -> MagicMock:
        """Create a mock ClaudeSDKClient."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
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
                ):
                    runtime = ClaudeAgentRuntime(
                        socket_writer, transport_factory=lambda _: MagicMock()
                    )
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
    async def test_socket_writer_serializes_concurrent_sends(self) -> None:
        """Concurrent send calls should not overlap socket drains."""
        active_drains = 0
        max_active_drains = 0
        raw_writer = MagicMock()

        async def drain() -> None:
            nonlocal active_drains, max_active_drains
            active_drains += 1
            max_active_drains = max(max_active_drains, active_drains)
            await asyncio.sleep(0)
            active_drains -= 1

        raw_writer.write = MagicMock()
        raw_writer.drain = AsyncMock(side_effect=drain)
        socket_writer = SocketStreamWriter(cast(asyncio.StreamWriter, raw_writer))

        await asyncio.gather(
            socket_writer.send_stream_event(
                UnifiedStreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    text="hello",
                    part_id=0,
                )
            ),
            socket_writer.send_session_line("sdk-session", "{}"),
            socket_writer.send_done(),
        )

        assert raw_writer.write.call_count == 3
        assert max_active_drains == 1

    @pytest.mark.anyio
    async def test_session_update_and_log_envelopes_round_trip(self) -> None:
        """Session updates and structured logs survive the socket framing."""
        raw_writer = MagicMock()
        raw_writer.write = MagicMock()
        raw_writer.drain = AsyncMock()
        socket_writer = SocketStreamWriter(cast(asyncio.StreamWriter, raw_writer))

        await socket_writer.send_session_update(
            sdk_session_id="test-session-123",
            sdk_session_data='{"message": "hello"}\n',
        )
        await socket_writer.send_log("info", "Runtime initialized", model="gpt-4")
        await socket_writer.send_log("debug", "Query sent", prompt_length=42)
        await socket_writer.send_log("warning", "Slow response detected")

        reader = asyncio.StreamReader()
        for written in raw_writer.write.call_args_list:
            reader.feed_data(written.args[0])
        reader.feed_eof()
        envelopes = [
            RuntimeEventEnvelope.from_dict(
                orjson.loads((await read_message(reader))[1])
            )
            for _ in range(4)
        ]

        session_update, *log_events = envelopes
        assert session_update.type == "session_update"
        assert session_update.sdk_session_id == "test-session-123"
        assert session_update.sdk_session_data == '{"message": "hello"}\n'
        assert [
            (event.type, event.log_level, event.log_message, event.log_extra)
            for event in log_events
        ] == [
            ("log", "info", "Runtime initialized", {"model": "gpt-4"}),
            ("log", "debug", "Query sent", {"prompt_length": 42}),
            ("log", "warning", "Slow response detected", None),
        ]
