"""Socket protocol models for orchestrator-runtime communication.

These models define the contract between the trusted orchestrator (outside NSJail)
and the sandboxed runtime (inside NSJail).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

from claude_agent_sdk.types import Message
from pydantic import TypeAdapter

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.stream.types import UnifiedStreamEvent
from tracecat.agent.types import AgentConfig


@dataclass(kw_only=True, slots=True)
class RuntimeInitPayload:
    """Payload sent from orchestrator to runtime on initialization.

    The orchestrator sends this after the runtime connects to the control socket.
    Contains everything the runtime needs to execute an agent turn.

    On resume after approval, the sdk_session_data contains the proper tool_result
    entry (inserted by execute_approved_tools_activity before reload), so the
    runtime just resumes normally.
    """

    # Session context
    session_id: uuid.UUID
    mcp_auth_token: str  # JWT for MCP auth
    config: AgentConfig
    user_prompt: str
    litellm_auth_token: str
    # Resolved tool definitions (orchestrator resolves action names → full definitions)
    allowed_actions: dict[str, MCPToolDefinition] | None = None
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None  # JSONL content for resume
    is_approval_continuation: bool = False  # True when resuming after approval decision


@dataclass(kw_only=True, slots=True)
class RuntimeEventEnvelope:
    """Envelope for events sent from runtime to orchestrator.

    All communication from runtime to orchestrator uses this envelope format.
    The `type` field determines which other fields are populated.

    Types:
    - stream_event: Partial streaming deltas (TEXT_DELTA, TOOL_CALL_DELTA, etc.)
    - message: Complete messages (UserMessage, AssistantMessage) for persistence
    - session_update: SDK session data for persistence
    - error: Runtime error
    - done: Completion signal

    Note: The runtime is untrusted - the loopback/orchestrator adds session_id
    when persisting messages to ensure proper authorization.
    """

    type: Literal[
        "stream_event",
        "message",
        "session_line",
        "session_update",
        "result",
        "error",
        "done",
        "log",
    ]
    event: UnifiedStreamEvent | None = None  # For type="stream_event"
    message: dict[str, Any] | None = None  # For type="message" (serialized Message)
    session_line: str | None = None  # For type="session_line" (raw JSONL line)
    internal: bool = (
        False  # For type="session_line" - if True, not shown in UI timeline
    )
    sdk_session_id: str | None = None  # For type="session_update" or "session_line"
    sdk_session_data: str | None = None  # For type="session_update"
    error: str | None = None  # For type="error"
    # For type="result" - final usage data from Claude SDK ResultMessage
    result_usage: dict[str, Any] | None = None
    result_num_turns: int | None = None
    result_duration_ms: int | None = None
    # For type="log" - structured log forwarding from sandbox
    log_level: str | None = None  # "debug", "info", "warning", "error"
    log_message: str | None = None
    log_extra: dict[str, Any] | None = None

    @classmethod
    def from_stream_event(cls, event: UnifiedStreamEvent) -> RuntimeEventEnvelope:
        """Create a stream event envelope for partial deltas."""
        return cls(type="stream_event", event=event)

    @classmethod
    def from_message(cls, message: Message) -> RuntimeEventEnvelope:
        """Create a message envelope for complete messages.

        Serializes the Claude SDK message. The loopback adds uuid/sessionId
        when persisting (runtime is untrusted).
        """
        data = asdict(message)
        # Add type field based on message class name (e.g., UserMessage -> user)
        data["type"] = type(message).__name__.lower().replace("message", "")
        return cls(type="message", message=data)

    @classmethod
    def from_session_line(
        cls, sdk_session_id: str, line: str, *, internal: bool = False
    ) -> RuntimeEventEnvelope:
        """Create a session line envelope for raw JSONL persistence.

        This carries a complete JSONL line from the Claude SDK session file,
        preserving the exact format needed for resume (uuid, timestamp, parentUuid, etc.).

        Args:
            sdk_session_id: The SDK's internal session ID.
            line: Raw JSONL line from the SDK session file.
            internal: If True, this line is internal state and should not appear
                in the UI timeline (e.g., interrupts, synthetic messages, compaction).
        """
        return cls(
            type="session_line",
            session_line=line,
            sdk_session_id=sdk_session_id,
            internal=internal,
        )

    @classmethod
    def from_session_update(
        cls, sdk_session_id: str, sdk_session_data: str
    ) -> RuntimeEventEnvelope:
        """Create a session update envelope."""
        return cls(
            type="session_update",
            sdk_session_id=sdk_session_id,
            sdk_session_data=sdk_session_data,
        )

    @classmethod
    def from_result(
        cls,
        usage: dict[str, Any] | None = None,
        num_turns: int | None = None,
        duration_ms: int | None = None,
    ) -> RuntimeEventEnvelope:
        """Create a result envelope with usage data from Claude SDK ResultMessage."""
        return cls(
            type="result",
            result_usage=usage,
            result_num_turns=num_turns,
            result_duration_ms=duration_ms,
        )

    @classmethod
    def from_error(cls, error: str) -> RuntimeEventEnvelope:
        """Create an error envelope."""
        return cls(type="error", error=error)

    @classmethod
    def done(cls) -> RuntimeEventEnvelope:
        """Create a done envelope."""
        return cls(type="done")

    @classmethod
    def from_log(
        cls, level: str, message: str, extra: dict[str, Any] | None = None
    ) -> RuntimeEventEnvelope:
        """Create a log envelope for structured log forwarding."""
        return cls(type="log", log_level=level, log_message=message, log_extra=extra)


RuntimeInitPayloadTA: TypeAdapter[RuntimeInitPayload] = TypeAdapter(RuntimeInitPayload)
RuntimeEventEnvelopeTA: TypeAdapter[RuntimeEventEnvelope] = TypeAdapter(
    RuntimeEventEnvelope
)
