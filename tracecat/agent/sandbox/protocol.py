"""Socket protocol models for orchestrator-runtime communication.

These models define the contract between the trusted orchestrator (outside NSJail)
and the sandboxed runtime (inside NSJail).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import TypeAdapter

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.stream.types import UnifiedStreamEvent
from tracecat.agent.types import AgentConfig


@dataclass(kw_only=True, slots=True)
class RuntimeInitPayload:
    """Payload sent from orchestrator to runtime on initialization.

    The orchestrator sends this after the runtime connects to the control socket.
    Contains everything the runtime needs to execute an agent turn.
    """

    # Session context
    session_id: uuid.UUID
    mcp_socket_path: str  # Path to trusted MCP server socket
    jwt_token: str  # JWT for MCP auth
    config: AgentConfig
    user_prompt: str
    litellm_base_url: str
    litellm_auth_token: str
    # Resolved tool definitions (orchestrator resolves action names → full definitions)
    allowed_actions: dict[str, MCPToolDefinition] | None = None
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None  # JSONL content for resume


@dataclass(kw_only=True, slots=True)
class RuntimeEventEnvelope:
    """Envelope for events sent from runtime to orchestrator.

    All communication from runtime to orchestrator uses this envelope format.
    The `type` field determines which other fields are populated.
    """

    type: Literal["event", "session_update", "error", "done"]
    event: UnifiedStreamEvent | None = None  # For type="event"
    sdk_session_id: str | None = None  # For type="session_update"
    sdk_session_data: str | None = None  # For type="session_update"
    error: str | None = None  # For type="error"

    @classmethod
    def from_event(cls, event: UnifiedStreamEvent) -> RuntimeEventEnvelope:
        """Create an event envelope."""
        return cls(type="event", event=event)

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
    def from_error(cls, error: str) -> RuntimeEventEnvelope:
        """Create an error envelope."""
        return cls(type="error", error=error)

    @classmethod
    def done(cls) -> RuntimeEventEnvelope:
        """Create a done envelope."""
        return cls(type="done")


@dataclass(kw_only=True, slots=True)
class ApprovalContinuationPayload:
    """Payload for continuing after approval decisions.

    When the orchestrator receives approval decisions from the user,
    it executes the approved tools and sends this payload to spawn
    a new runtime with the continuation.
    """

    continuation_message: str  # The continuation message containing tool results
    sdk_session_id: str | None = None  # Updated session data (if any)
    sdk_session_data: str | None = None
    tool_results: list[dict[str, Any]] | None = None  # Tool results from approved tools


# TypeAdapters for JSON parsing (dataclasses don't have built-in JSON support)
RuntimeInitPayloadTA: TypeAdapter[RuntimeInitPayload] = TypeAdapter(RuntimeInitPayload)
RuntimeEventEnvelopeTA: TypeAdapter[RuntimeEventEnvelope] = TypeAdapter(
    RuntimeEventEnvelope
)
ApprovalContinuationPayloadTA: TypeAdapter[ApprovalContinuationPayload] = TypeAdapter(
    ApprovalContinuationPayload
)
