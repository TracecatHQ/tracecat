"""Lightweight stream types for agent communication.

Pure dataclasses with no Pydantic dependencies for minimal import footprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, cast, get_args

from tracecat.agent.approvals.types import PersistedApprovalDecision
from tracecat.agent.common.wire import (
    boolean,
    optional_integer,
    optional_object,
    optional_string,
    required_object,
    required_string,
)

type ArtifactEventOp = Literal["upsert", "remove"]
type ApprovalStreamStatus = Literal["pending", "approved", "rejected"]

_ARTIFACT_EVENT_OPS: frozenset[str] = frozenset(get_args(ArtifactEventOp.__value__))
_APPROVAL_STREAM_STATUSES: frozenset[str] = frozenset(
    get_args(ApprovalStreamStatus.__value__)
)


def _parse_approval_decision(
    value: object,
    *,
    path: str,
) -> PersistedApprovalDecision | None:
    if value is None or isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON boolean or object")

    decision = cast(dict[str, Any], value)
    if "kind" in decision:
        kind = required_string(decision, "kind", path=path)
        if kind == "tool-approved":
            optional_object(decision, "metadata", path=path)
        elif kind == "tool-denied":
            optional_string(decision, "message", path=path)
            optional_object(decision, "metadata", path=path)
        else:
            raise ValueError(f"{path}.kind has an unknown approval decision type")
    elif "value" in decision:
        if not isinstance(decision["value"], bool):
            raise ValueError(f"{path}.value must be a JSON boolean")
        metadata = decision.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError(f"{path}.metadata must be a JSON object")
    else:
        raise ValueError(f"{path} has an unknown approval decision shape")

    return cast(PersistedApprovalDecision, decision)


@dataclass(frozen=True, slots=True)
class VercelFrameCursor:
    """Browser SSE cursor for a Vercel frame fanned out from one Redis entry."""

    redis_id: str
    frame_index: int


def parse_vercel_frame_cursor(event_id: str | None) -> VercelFrameCursor | None:
    """Parse ``<redis-id>:<frame-index>`` cursors emitted by the Vercel adapter."""
    if not event_id:
        return None
    redis_id, separator, frame_index = event_id.rpartition(":")
    if not separator or not redis_id or not frame_index.isdecimal():
        return None
    return VercelFrameCursor(redis_id=redis_id, frame_index=int(frame_index))


class HarnessType(StrEnum):
    """Supported agent harnesses."""

    PYDANTIC_AI = "pydantic-ai"
    CLAUDE_CODE = "claude_code"


class StreamEventType(StrEnum):
    """Types of streaming events."""

    # Text streaming
    TEXT_START = "text_start"
    TEXT_DELTA = "text_delta"
    TEXT_STOP = "text_stop"

    # Thinking/reasoning streaming
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_STOP = "thinking_stop"

    # Tool events
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_STOP = "tool_call_stop"
    TOOL_RESULT = "tool_result"

    # Lifecycle events
    MESSAGE_START = "message_start"
    MESSAGE_STOP = "message_stop"
    USER_MESSAGE = "user_message"

    # System/status events
    COMPACTION = "compaction"
    ARTIFACT = "artifact"
    CANCELLED = "cancelled"

    # Control events
    ERROR = "error"
    DONE = "done"
    APPROVAL_REQUEST = "approval_request"


@dataclass(kw_only=True, slots=True)
class ToolCallContent:
    """Structured tool call for approval requests.

    This is the harness-agnostic representation of a tool call
    that requires approval before execution.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str
    """Unique tool call ID."""
    name: str
    """Fully-qualified tool name."""
    input: dict[str, Any] = field(default_factory=dict)
    """Arguments for the tool call."""
    metadata: dict[str, Any] | None = None
    """Trusted runtime metadata about the tool call scope."""
    status: ApprovalStreamStatus | None = None
    """Current approval status, when replaying persisted approval state."""
    decision: PersistedApprovalDecision | None = None
    """Persisted decision payload, when one exists."""
    reason: str | None = None
    """Persisted rejection reason, when one exists."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCallContent:
        """Construct from dict (orjson parsed)."""
        raw_type = data.get("type", "tool_call")
        if raw_type != "tool_call":
            raise ValueError("stream_event.approval_items[].type must be 'tool_call'")

        status = optional_string(
            data,
            "status",
            path="stream_event.approval_items[]",
        )
        if status is not None and status not in _APPROVAL_STREAM_STATUSES:
            raise ValueError(
                "stream_event.approval_items[].status has an unknown approval status"
            )

        return cls(
            type="tool_call",
            id=required_string(
                data,
                "id",
                path="stream_event.approval_items[]",
            ),
            name=required_string(
                data,
                "name",
                path="stream_event.approval_items[]",
            ),
            input=optional_object(
                data,
                "input",
                path="stream_event.approval_items[]",
            )
            or {},
            metadata=optional_object(
                data,
                "metadata",
                path="stream_event.approval_items[]",
            ),
            status=cast(ApprovalStreamStatus | None, status),
            decision=_parse_approval_decision(
                data.get("decision"),
                path="stream_event.approval_items[].decision",
            ),
            reason=optional_string(
                data,
                "reason",
                path="stream_event.approval_items[]",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        result: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }
        if self.metadata is not None:
            result["metadata"] = self.metadata
        if self.status is not None:
            result["status"] = self.status
        if self.decision is not None:
            result["decision"] = self.decision
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(kw_only=True, slots=True)
class ArtifactEventContent:
    """Artifact operation surfaced by agent runtimes."""

    op: ArtifactEventOp
    artifact: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactEventContent:
        """Construct from dict (orjson parsed)."""
        raw_op = required_string(data, "op", path="stream_event.artifact_data")
        if raw_op not in _ARTIFACT_EVENT_OPS:
            raise ValueError("stream_event.artifact_data.op has an unknown operation")
        artifact = required_object(data, "artifact", path="stream_event.artifact_data")
        return cls(op=cast(ArtifactEventOp, raw_op), artifact=artifact)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        return {
            "op": self.op,
            "artifact": self.artifact,
        }


@dataclass(kw_only=True, slots=True)
class UnifiedStreamEvent:
    """A normalized streaming event.

    All harnesses convert their native events to this format.
    Format adapters (vercel, basic) can consume this directly.
    """

    type: StreamEventType
    part_id: int | None = None
    """Index linking related events (e.g., start/delta/stop)."""

    # Flat payloads - only relevant ones are set based on event type
    text: str | None = None
    thinking: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    is_error: bool = False
    error: str | None = None
    metadata: dict[str, Any] | None = None

    # For APPROVAL_REQUEST events
    approval_items: list[ToolCallContent] | None = None

    # For ARTIFACT events
    artifact_data: ArtifactEventContent | None = None

    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedStreamEvent:
        """Construct from dict (orjson parsed)."""
        raw_type = required_string(data, "type", path="stream_event")
        try:
            event_type = StreamEventType(raw_type)
        except ValueError as exc:
            raise ValueError("stream_event.type has an unknown event type") from exc

        approval_items = None
        raw_approval_items = data.get("approval_items")
        if raw_approval_items is not None and not isinstance(raw_approval_items, list):
            raise ValueError("stream_event.approval_items must be a JSON array")
        if raw_approval_items:
            if not all(isinstance(item, dict) for item in raw_approval_items):
                raise ValueError(
                    "stream_event.approval_items entries must be JSON objects"
                )
            approval_items = [
                ToolCallContent.from_dict(cast(dict[str, Any], item))
                for item in raw_approval_items
            ]
        if event_type is StreamEventType.APPROVAL_REQUEST and not approval_items:
            raise ValueError(
                "stream_event.approval_request must include at least one approval item"
            )

        artifact_data = None
        raw_artifact_data = data.get("artifact_data")
        if raw_artifact_data is not None:
            if not isinstance(raw_artifact_data, dict):
                raise ValueError("stream_event.artifact_data must be a JSON object")
            artifact_data = ArtifactEventContent.from_dict(
                cast(dict[str, Any], raw_artifact_data)
            )

        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now(UTC)
        elif not isinstance(timestamp, datetime):
            raise ValueError("stream_event.timestamp must be an ISO 8601 string")

        is_error = boolean(data, "is_error", path="stream_event")

        return cls(
            type=event_type,
            part_id=optional_integer(data, "part_id", path="stream_event"),
            text=optional_string(data, "text", path="stream_event"),
            thinking=optional_string(data, "thinking", path="stream_event"),
            tool_call_id=optional_string(
                data,
                "tool_call_id",
                path="stream_event",
            ),
            tool_name=optional_string(data, "tool_name", path="stream_event"),
            tool_input=optional_object(data, "tool_input", path="stream_event"),
            tool_output=data.get("tool_output"),
            is_error=is_error,
            error=optional_string(data, "error", path="stream_event"),
            metadata=optional_object(data, "metadata", path="stream_event"),
            approval_items=approval_items,
            artifact_data=artifact_data,
            timestamp=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        result: dict[str, Any] = {
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.part_id is not None:
            result["part_id"] = self.part_id
        if self.text is not None:
            result["text"] = self.text
        if self.thinking is not None:
            result["thinking"] = self.thinking
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_name is not None:
            result["tool_name"] = self.tool_name
        if self.tool_input is not None:
            result["tool_input"] = self.tool_input
        if self.tool_output is not None:
            result["tool_output"] = self.tool_output
        if self.is_error:
            result["is_error"] = self.is_error
        if self.error is not None:
            result["error"] = self.error
        if self.metadata is not None:
            result["metadata"] = self.metadata
        if self.approval_items is not None:
            result["approval_items"] = [item.to_dict() for item in self.approval_items]
        if self.artifact_data is not None:
            result["artifact_data"] = self.artifact_data.to_dict()
        return result

    @classmethod
    def approval_request_event(cls, items: list[ToolCallContent]) -> UnifiedStreamEvent:
        """Factory method for creating approval request events."""
        return cls(type=StreamEventType.APPROVAL_REQUEST, approval_items=items)

    @classmethod
    def user_message_event(cls, content: str) -> UnifiedStreamEvent:
        """Factory method for creating user message events."""
        return cls(type=StreamEventType.USER_MESSAGE, text=content)

    @classmethod
    def compaction_event(
        cls,
        *,
        phase: Literal["started", "completed", "failed"],
        metadata: dict[str, Any] | None = None,
    ) -> UnifiedStreamEvent:
        """Factory method for creating compaction status events."""
        event_metadata: dict[str, Any] = {"phase": phase}
        if metadata:
            event_metadata.update(metadata)
        return cls(
            type=StreamEventType.COMPACTION,
            metadata=event_metadata,
        )

    @classmethod
    def cancelled_event(
        cls,
        *,
        reason: str | None = None,
        tool_call_ids: list[str] | None = None,
    ) -> UnifiedStreamEvent:
        """Factory method for creating turn-cancelled status events.

        Args:
            reason: Human/machine-readable cancellation reason.
            tool_call_ids: Tool calls the interrupt aborted mid-flight. Clients
                use these to render the affected tool calls as "interrupted"
                instead of surfacing SDK abort artifacts as tool errors.
        """
        metadata: dict[str, Any] = {}
        if reason is not None:
            metadata["reason"] = reason
        if tool_call_ids:
            metadata["tool_call_ids"] = list(tool_call_ids)
        return cls(type=StreamEventType.CANCELLED, metadata=metadata)

    @classmethod
    def tool_result_event(
        cls,
        tool_call_id: str,
        tool_name: str,
        output: Any,
        is_error: bool = False,
    ) -> UnifiedStreamEvent:
        """Factory method for creating tool result events."""
        return cls(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_output=output,
            is_error=is_error,
        )

    @classmethod
    def artifact_event(
        cls,
        *,
        op: ArtifactEventOp,
        artifact: dict[str, Any],
    ) -> UnifiedStreamEvent:
        """Factory method for creating artifact stream events."""
        return cls(
            type=StreamEventType.ARTIFACT,
            artifact_data=ArtifactEventContent(op=op, artifact=artifact),
        )
