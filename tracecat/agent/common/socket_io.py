"""Socket I/O utilities for sandboxed runtime.

Provides stream writing capabilities over Unix sockets for the runtime
to communicate with the orchestrator using a typed message protocol.

Protocol: msg_type (1B) | length (4B) | payload (length B)

- msg_type: Message type identifier (1 byte)
- length: Payload length in bytes, big-endian (4 bytes, max ~4 GiB)
- payload: JSON-encoded message body

Uses orjson for serialization (no pydantic_core).
"""

from __future__ import annotations

import asyncio
from enum import IntEnum
from typing import Any

import orjson

from tracecat.agent.common.protocol import RuntimeEventEnvelope
from tracecat.agent.common.stream_types import UnifiedStreamEvent

# Header size: 1 byte msg_type + 4 bytes length
HEADER_SIZE = 5

# Max payload size to prevent DoS from compromised runtime (100 MiB)
MAX_PAYLOAD_SIZE = 100 * 1024 * 1024


class MessageType(IntEnum):
    """Message types for orchestrator-runtime protocol."""

    # Orchestrator → Runtime
    INIT = 0x01  # RuntimeInitPayload

    # Runtime → Orchestrator
    EVENT = 0x10  # RuntimeEventEnvelope


def build_message(msg_type: MessageType, payload: bytes) -> bytes:
    """Build a framed message with header and payload.

    Args:
        msg_type: The message type identifier.
        payload: The JSON-encoded payload bytes.

    Returns:
        Complete message with header prefix.
    """
    header = bytes([msg_type]) + len(payload).to_bytes(4, "big")
    return header + payload


async def read_message(
    reader: asyncio.StreamReader,
    expected_type: MessageType | None = None,
) -> tuple[MessageType, bytes]:
    """Read a framed message from the socket.

    Args:
        reader: The asyncio stream reader.
        expected_type: If provided, validates the message type matches.

    Returns:
        Tuple of (message_type, payload_bytes).

    Raises:
        RuntimeError: If message type is unknown or unexpected.
        ValueError: If payload exceeds MAX_PAYLOAD_SIZE.
        asyncio.IncompleteReadError: If connection closed mid-read.
    """
    # Read header
    header = await reader.readexactly(HEADER_SIZE)
    msg_type_byte = header[0]
    payload_length = int.from_bytes(header[1:5], "big")

    # Validate payload size to prevent DoS
    if payload_length > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload size {payload_length} exceeds max {MAX_PAYLOAD_SIZE}"
        )

    # Validate message type
    try:
        msg_type = MessageType(msg_type_byte)
    except ValueError as e:
        raise RuntimeError(f"Unknown message type: 0x{msg_type_byte:02x}") from e

    if expected_type is not None and msg_type != expected_type:
        raise RuntimeError(
            f"Expected message type {expected_type.name}, got {msg_type.name}"
        )

    # Read payload
    if payload_length == 0:
        return msg_type, b""

    payload = await reader.readexactly(payload_length)
    return msg_type, payload


class SocketStreamWriter:
    """Writes events to orchestrator via Unix socket.

    This is used by the sandboxed runtime to stream events back to the
    trusted orchestrator using the typed message protocol.
    """

    def __init__(self, writer: asyncio.StreamWriter):
        self._writer = writer

    async def _send(self, envelope: RuntimeEventEnvelope) -> None:
        """Send an envelope over the socket."""
        payload = orjson.dumps(envelope.to_dict())
        message = build_message(MessageType.EVENT, payload)
        self._writer.write(message)
        await self._writer.drain()

    async def send_stream_event(self, event: UnifiedStreamEvent) -> None:
        """Send a stream event (partial delta) to the orchestrator."""
        await self._send(RuntimeEventEnvelope.from_stream_event(event))

    async def send_message(self, message: Any) -> None:
        """Send a complete message to the orchestrator for persistence.

        The loopback adds uuid/sessionId when persisting (runtime is untrusted).

        NOTE: This sends the inner message only. For full JSONL persistence with
        proper uuid/timestamp/parentUuid, use send_session_line() instead.
        """
        await self._send(RuntimeEventEnvelope.from_message(message))

    async def send_session_line(
        self, sdk_session_id: str, line: str, *, internal: bool = False
    ) -> None:
        """Send a raw JSONL line for persistence.

        This sends a complete JSONL line from the Claude SDK session file,
        preserving the exact format needed for resume (uuid, timestamp, parentUuid, etc.).

        Args:
            sdk_session_id: The SDK's internal session ID.
            line: Raw JSONL line from the SDK session file.
            internal: If True, this is internal state not shown in UI timeline.
        """
        await self._send(
            RuntimeEventEnvelope.from_session_line(
                sdk_session_id, line, internal=internal
            )
        )

    async def send_session_update(
        self,
        sdk_session_id: str,
        sdk_session_data: str,
    ) -> None:
        """Send session update for persistence by orchestrator."""
        await self._send(
            RuntimeEventEnvelope.from_session_update(sdk_session_id, sdk_session_data)
        )

    async def send_result(
        self,
        usage: dict[str, Any] | None = None,
        num_turns: int | None = None,
        duration_ms: int | None = None,
        structured_output: Any = None,
    ) -> None:
        """Send final result with usage data from Claude SDK ResultMessage."""
        await self._send(
            RuntimeEventEnvelope.from_result(
                usage=usage,
                num_turns=num_turns,
                duration_ms=duration_ms,
                structured_output=structured_output,
            )
        )

    async def send_error(self, error: str) -> None:
        """Send error event to the orchestrator."""
        await self._send(RuntimeEventEnvelope.from_error(error))

    async def send_done(self) -> None:
        """Send done event to signal completion."""
        await self._send(RuntimeEventEnvelope.done())

    async def send_log(self, level: str, message: str, **extra: object) -> None:
        """Send a structured log event to the orchestrator.

        This forwards logs from inside the sandbox to the worker's logger,
        providing visibility into sandbox execution.

        Args:
            level: Log level ("debug", "info", "warning", "error").
            message: The log message.
            **extra: Additional structured fields to include.
        """
        # Convert extra values to JSON-serializable types
        extra_dict: dict[str, Any] | None = None
        if extra:
            extra_dict = dict(extra)
        await self._send(RuntimeEventEnvelope.from_log(level, message, extra_dict))

    async def close(self) -> None:
        """Close the socket connection."""
        self._writer.close()
        await self._writer.wait_closed()
