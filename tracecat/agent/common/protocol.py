"""Socket protocol models for orchestrator-runtime communication.

These models define the contract between the trusted orchestrator (outside NSJail)
and the sandboxed runtime (inside NSJail).

Uses pure dataclasses with orjson for minimal import footprint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from tracecat.agent.common.stream_types import UnifiedStreamEvent
from tracecat.agent.common.types import (
    MCPCommandServerConfig,
    MCPToolDefinition,
    SandboxAgentConfig,
)


@dataclass(kw_only=True, slots=True)
class RuntimeInitPayload:
    """Payload sent from orchestrator to runtime on initialization.

    The orchestrator sends this after the runtime connects to the control socket.
    Contains everything the runtime needs to execute an agent turn.

    On resume after approval, the sdk_session_data contains the proper tool_result
    entry (inserted by execute_approved_tools_activity before reload), so the
    runtime just resumes normally.
    """

    # Runtime selection
    runtime_type: str = "claude_code"
    """Runtime type to load (e.g., 'claude_code', 'openai_agents')."""

    # Session context
    session_id: uuid.UUID
    mcp_auth_token: str  # JWT for MCP auth
    config: SandboxAgentConfig
    user_prompt: str
    litellm_auth_token: str

    # Resolved tool definitions (orchestrator resolves action names → full definitions)
    allowed_actions: dict[str, MCPToolDefinition] | None = None
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None  # JSONL content for resume
    is_approval_continuation: bool = False  # True when resuming after approval decision
    is_fork: bool = False
    # Command-based MCP servers (stdio) - run as subprocesses inside the sandbox
    mcp_command_servers: list[MCPCommandServerConfig] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeInitPayload:
        """Construct from dict (orjson parsed)."""
        # Parse config
        config = SandboxAgentConfig.from_dict(data["config"])

        # Parse allowed_actions
        allowed_actions = None
        if data.get("allowed_actions"):
            allowed_actions = {
                k: MCPToolDefinition.from_dict(v)
                for k, v in data["allowed_actions"].items()
            }

        # Parse session_id
        session_id = data["session_id"]
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)

        return cls(
            runtime_type=data.get("runtime_type", "claude_code"),
            session_id=session_id,
            mcp_auth_token=data["mcp_auth_token"],
            config=config,
            user_prompt=data["user_prompt"],
            litellm_auth_token=data["litellm_auth_token"],
            allowed_actions=allowed_actions,
            sdk_session_id=data.get("sdk_session_id"),
            sdk_session_data=data.get("sdk_session_data"),
            is_approval_continuation=data.get("is_approval_continuation", False),
            is_fork=data.get("is_fork", False),
            mcp_command_servers=data.get("mcp_command_servers"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        result: dict[str, Any] = {
            "runtime_type": self.runtime_type,
            "session_id": str(self.session_id),
            "mcp_auth_token": self.mcp_auth_token,
            "config": self.config.to_dict(),
            "user_prompt": self.user_prompt,
            "litellm_auth_token": self.litellm_auth_token,
            "is_approval_continuation": self.is_approval_continuation,
            "is_fork": self.is_fork,
        }
        if self.allowed_actions is not None:
            result["allowed_actions"] = {
                k: v.to_dict() for k, v in self.allowed_actions.items()
            }
        if self.sdk_session_id is not None:
            result["sdk_session_id"] = self.sdk_session_id
        if self.sdk_session_data is not None:
            result["sdk_session_data"] = self.sdk_session_data
        if self.mcp_command_servers is not None:
            result["mcp_command_servers"] = self.mcp_command_servers
        return result


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
    result_output: Any = None
    # For type="log" - structured log forwarding from sandbox
    log_level: str | None = None  # "debug", "info", "warning", "error"
    log_message: str | None = None
    log_extra: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeEventEnvelope:
        """Construct from dict (orjson parsed)."""
        event = None
        if data.get("event"):
            event = UnifiedStreamEvent.from_dict(data["event"])

        return cls(
            type=data["type"],
            event=event,
            message=data.get("message"),
            session_line=data.get("session_line"),
            internal=data.get("internal", False),
            sdk_session_id=data.get("sdk_session_id"),
            sdk_session_data=data.get("sdk_session_data"),
            error=data.get("error"),
            result_usage=data.get("result_usage"),
            result_num_turns=data.get("result_num_turns"),
            result_duration_ms=data.get("result_duration_ms"),
            result_output=data.get(
                "result_output",
                data.get("result_structured_output", data.get("result_result")),
            ),
            log_level=data.get("log_level"),
            log_message=data.get("log_message"),
            log_extra=data.get("log_extra"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        result: dict[str, Any] = {"type": self.type}
        if self.event is not None:
            result["event"] = self.event.to_dict()
        if self.message is not None:
            result["message"] = self.message
        if self.session_line is not None:
            result["session_line"] = self.session_line
        if self.internal:
            result["internal"] = self.internal
        if self.sdk_session_id is not None:
            result["sdk_session_id"] = self.sdk_session_id
        if self.sdk_session_data is not None:
            result["sdk_session_data"] = self.sdk_session_data
        if self.error is not None:
            result["error"] = self.error
        if self.result_usage is not None:
            result["result_usage"] = self.result_usage
        if self.result_num_turns is not None:
            result["result_num_turns"] = self.result_num_turns
        if self.result_duration_ms is not None:
            result["result_duration_ms"] = self.result_duration_ms
        if self.result_output is not None:
            result["result_output"] = self.result_output
        if self.log_level is not None:
            result["log_level"] = self.log_level
        if self.log_message is not None:
            result["log_message"] = self.log_message
        if self.log_extra is not None:
            result["log_extra"] = self.log_extra
        return result

    @classmethod
    def from_stream_event(cls, event: UnifiedStreamEvent) -> RuntimeEventEnvelope:
        """Create a stream event envelope for partial deltas."""
        return cls(type="stream_event", event=event)

    @classmethod
    def from_message(cls, message: Any) -> RuntimeEventEnvelope:
        """Create a message envelope for complete messages.

        Serializes the Claude SDK message. The loopback adds uuid/sessionId
        when persisting (runtime is untrusted).
        """
        from dataclasses import asdict

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
        output: Any = None,
    ) -> RuntimeEventEnvelope:
        """Create a result envelope with usage data from Claude SDK ResultMessage."""
        return cls(
            type="result",
            result_usage=usage,
            result_num_turns=num_turns,
            result_duration_ms=duration_ms,
            result_output=output,
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
