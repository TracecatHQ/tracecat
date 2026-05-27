"""Data models and adapter logic for the Vercel AI SDK. Taken from https://gist.github.com/183amir/ce45cf52f034b493fac0bb4b1838236a
Spec: https://sdk.vercel.ai/docs/concepts/stream-protocol#data-stream-protocol
Data models: https://github.com/vercel/ai/blob/b024298c/packages/ai/src/ui/ui-messages.ts
As of 18.09.2025 full implementation of this is being added to pydantic AI in https://github.com/pydantic/pydantic-ai/pull/2923
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NotRequired,
    TypedDict,
    TypeGuard,
    cast,
)

import pydantic
from claude_agent_sdk.types import (
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_core import to_json

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.mcp.metadata import strip_proxy_tool_metadata
from tracecat.agent.mcp.utils import normalize_mcp_tool_name
from tracecat.agent.stream.events import (
    StreamDelta,
    StreamEnd,
    StreamError,
    StreamEvent,
    StreamKeepAlive,
)
from tracecat.agent.types import UnifiedMessage
from tracecat.chat.constants import (
    APPROVAL_DATA_PART_TYPE,
    APPROVAL_REQUEST_HEADER,
    COMPACTION_DATA_PART_TYPE,
)
from tracecat.chat.enums import MessageKind
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.chat.schemas import ChatMessage
# Using a type alias for ProviderMetadata since its structure is not defined.
ProviderMetadata = dict[str, dict[str, Any]]


def _extract_structured_error(output: Any) -> str | None:
    """Extract error message from structured error format.

    The MCP proxy server returns errors as JSON: {"success": false, "error": "..."}
    This is a workaround for Claude Agent SDK not propagating is_error flag.

    The output can come in several formats:
    1. Direct string: '{"success": false, "error": "..."}'
    2. MCP content array: [{"type": "text", "text": '{"success": false, ...}'}]
    3. Already parsed dict with success/error keys

    Args:
        output: Tool output which may contain structured error in various formats.

    Returns:
        Error message if structured error detected, None otherwise.
    """
    text_to_parse: str | None = None

    # Handle MCP content array format: [{"type": "text", "text": "..."}]
    if isinstance(output, list) and len(output) > 0:
        first_item = output[0]
        if isinstance(first_item, dict) and first_item.get("type") == "text":
            text_to_parse = first_item.get("text")

    # Handle direct string
    elif isinstance(output, str):
        text_to_parse = output

    # Handle already-parsed dict
    elif isinstance(output, dict):
        if output.get("success") is False and isinstance(output.get("error"), str):
            return output["error"]
        return None

    if not text_to_parse:
        return None

    try:
        parsed = json.loads(text_to_parse)
        if (
            isinstance(parsed, dict)
            and parsed.get("success") is False
            and isinstance(parsed.get("error"), str)
        ):
            return parsed["error"]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


# ==============================================================================
# 1. Models for UI Parts with Fixed 'type' Literals
# ==============================================================================


class TextUIPart(TypedDict):
    """A text part of a message."""

    type: Literal["text"]
    text: str
    state: NotRequired[Literal["streaming", "done"]]
    providerMetadata: NotRequired[ProviderMetadata]


class ReasoningUIPart(TypedDict):
    """A reasoning part of a message."""

    type: Literal["reasoning"]
    text: str
    state: NotRequired[Literal["streaming", "done"]]
    providerMetadata: NotRequired[ProviderMetadata]


class SourceUrlUIPart(TypedDict):
    """A source URL part of a message."""

    type: Literal["source-url"]
    sourceId: str
    url: str
    title: NotRequired[str]
    providerMetadata: NotRequired[ProviderMetadata]


class SourceDocumentUIPart(TypedDict):
    """A document source part of a message."""

    type: Literal["source-document"]
    sourceId: str
    mediaType: str
    title: str
    filename: NotRequired[str]
    providerMetadata: NotRequired[ProviderMetadata]


class FileUIPart(TypedDict):
    """A file part of a message."""

    type: Literal["file"]
    mediaType: str
    url: str
    filename: NotRequired[str]
    providerMetadata: NotRequired[ProviderMetadata]


class StepStartUIPart(TypedDict):
    """A step boundary part of a message."""

    type: Literal["step-start"]


# ==============================================================================
# 2. Models for Complex Nested Parts (Tools)
# ==============================================================================

# -------------------------- DynamicToolUIPart ---------------------------------
# This part has a fixed 'type' but is internally a discriminated union on 'state'.


class DynamicToolUIPartInputStreaming(TypedDict):
    type: Literal["dynamic-tool"]
    toolName: str
    toolCallId: str
    state: Literal["input-streaming"]
    input: NotRequired[Any]
    output: NotRequired[None]
    errorText: NotRequired[None]


class DynamicToolUIPartInputAvailable(TypedDict):
    type: Literal["dynamic-tool"]
    toolName: str
    toolCallId: str
    state: Literal["input-available"]
    input: Any
    output: NotRequired[None]
    errorText: NotRequired[None]
    callProviderMetadata: NotRequired[ProviderMetadata]


class DynamicToolUIPartOutputAvailable(TypedDict):
    type: Literal["dynamic-tool"]
    toolName: str
    toolCallId: str
    state: Literal["output-available"]
    input: Any
    output: Any
    errorText: NotRequired[None]
    callProviderMetadata: NotRequired[ProviderMetadata]
    preliminary: NotRequired[bool]


class DynamicToolUIPartOutputError(TypedDict):
    type: Literal["dynamic-tool"]
    toolName: str
    toolCallId: str
    state: Literal["output-error"]
    input: Any
    output: NotRequired[None]
    errorText: str
    callProviderMetadata: NotRequired[ProviderMetadata]


# A union of all possible states for a DynamicToolUIPart.
# The individual models will be used in the final UIMessagePart union.
DynamicToolUIPart = (
    DynamicToolUIPartInputStreaming
    | DynamicToolUIPartInputAvailable
    | DynamicToolUIPartOutputAvailable
    | DynamicToolUIPartOutputError
)

# ==============================================================================
# 3. Models for UI Parts with Dynamic 'type' (using regex patterns)
# ==============================================================================


# ----------------------------- DataUIPart -------------------------------------
class DataUIPart(TypedDict):
    """A custom data part, where type matches 'data-...'."""

    type: Annotated[str, pydantic.Field(pattern=r"^data-.+$")]
    id: NotRequired[str]
    data: Any


# ----------------------------- ToolUIPart -------------------------------------
# This part has a dynamic 'type' ('tool-...') and is also a discriminated
# union on 'state'.


class ToolUIPartInputStreaming(TypedDict):
    type: str
    toolCallId: str
    state: Literal["input-streaming"]
    input: NotRequired[Any]
    providerExecuted: NotRequired[bool]
    output: NotRequired[None]
    errorText: NotRequired[None]


class ToolUIPartInputAvailable(TypedDict):
    type: str
    toolCallId: str
    state: Literal["input-available"]
    input: Any
    providerExecuted: NotRequired[bool]
    output: NotRequired[None]
    errorText: NotRequired[None]
    callProviderMetadata: NotRequired[ProviderMetadata]


class ToolUIPartOutputAvailable(TypedDict):
    type: str
    toolCallId: str
    state: Literal["output-available"]
    input: Any
    output: Any
    errorText: NotRequired[None]
    providerExecuted: NotRequired[bool]
    callProviderMetadata: NotRequired[ProviderMetadata]
    preliminary: NotRequired[bool]


class ToolUIPartOutputError(TypedDict):
    type: str
    toolCallId: str
    state: Literal["output-error"]
    input: NotRequired[Any]
    rawInput: NotRequired[Any]
    output: NotRequired[None]
    errorText: str
    providerExecuted: NotRequired[bool]
    callProviderMetadata: NotRequired[ProviderMetadata]


# Union for the generic ToolUIPart, discriminated by 'state'.
ToolUIPart = (
    ToolUIPartInputStreaming
    | ToolUIPartInputAvailable
    | ToolUIPartOutputAvailable
    | ToolUIPartOutputError
)

# ==============================================================================
# 4. Final Union of All Message Part Models
# ==============================================================================

# The final UIMessagePart is a Union without nested discriminators.
# We list all variants explicitly to avoid OpenAPI schema generation issues
# with nested discriminated unions.
UIMessagePart = (
    TextUIPart
    | ReasoningUIPart
    | SourceUrlUIPart
    | SourceDocumentUIPart
    | FileUIPart
    | StepStartUIPart
    | DynamicToolUIPartInputStreaming
    | DynamicToolUIPartInputAvailable
    | DynamicToolUIPartOutputAvailable
    | DynamicToolUIPartOutputError
    | ToolUIPart
    | DataUIPart
)


# ==============================================================================
# Type Guards for UI Parts
# ==============================================================================


def is_text_ui_part(part: UIMessagePart) -> TypeGuard[TextUIPart]:
    return isinstance(part, dict) and part.get("type") == "text"


def is_reasoning_ui_part(part: UIMessagePart) -> TypeGuard[ReasoningUIPart]:
    return isinstance(part, dict) and part.get("type") == "reasoning"


def is_file_ui_part(part: UIMessagePart) -> TypeGuard[FileUIPart]:
    return isinstance(part, dict) and part.get("type") == "file"


def is_dynamic_tool_part(
    part: UIMessagePart | DynamicToolUIPart,
) -> TypeGuard[DynamicToolUIPart]:
    return isinstance(part, dict) and part.get("type") == "dynamic-tool"


def is_tool_part(part: UIMessagePart | ToolUIPart) -> TypeGuard[ToolUIPart]:
    return (
        isinstance(part, dict)
        and isinstance(part.get("type"), str)
        and part["type"].startswith("tool-")
    )


# ==============================================================================
# 5. Top-Level UIMessage Model
# ==============================================================================


class UIMessage(pydantic.BaseModel):
    """
    Pydantic model for AI SDK UI Messages, used for validation between
    frontend and backend.
    """

    id: str
    role: Literal["system", "user", "assistant"]
    metadata: Any | None = None
    parts: list[UIMessagePart]
    model_config = pydantic.ConfigDict(extra="forbid")


# ==============================================================================
# 6. Top-Level Request Body Model for FastAPI
# ==============================================================================


class VercelAIRequest(pydantic.BaseModel):
    """
    Represents the entire JSON body sent from the Vercel AI SDK frontend.
    """

    messages: list[UIMessage]
    model: str | None = None
    webSearch: bool | None = None
    trigger: str | None = None
    id: str | None = None  # The top-level ID seems to be the chat ID

    # Allow other fields that might be sent by the client
    model_config = pydantic.ConfigDict(extra="allow")


# ==============================================================================
# 7. Conversion Logic and Helper Functions
# ==============================================================================

# ==============================================================================
# 8. Vercel AI SDK Data Stream Protocol Adapter
# ==============================================================================


@dataclasses.dataclass(slots=True, kw_only=True)
class StartEventPayload:
    type: Literal["start"] = dataclasses.field(init=False, default="start")
    messageId: str


@dataclasses.dataclass(slots=True, kw_only=True)
class FinishEventPayload:
    type: Literal["finish"] = dataclasses.field(init=False, default="finish")


@dataclasses.dataclass(slots=True, kw_only=True)
class TextStartEventPayload:
    type: Literal["text-start"] = dataclasses.field(init=False, default="text-start")
    id: str


@dataclasses.dataclass(slots=True, kw_only=True)
class TextDeltaEventPayload:
    type: Literal["text-delta"] = dataclasses.field(init=False, default="text-delta")
    id: str
    delta: str


@dataclasses.dataclass(slots=True, kw_only=True)
class TextEndEventPayload:
    type: Literal["text-end"] = dataclasses.field(init=False, default="text-end")
    id: str


@dataclasses.dataclass(slots=True, kw_only=True)
class ReasoningStartEventPayload:
    type: Literal["reasoning-start"] = dataclasses.field(
        init=False, default="reasoning-start"
    )
    id: str


@dataclasses.dataclass(slots=True, kw_only=True)
class ReasoningDeltaEventPayload:
    type: Literal["reasoning-delta"] = dataclasses.field(
        init=False, default="reasoning-delta"
    )
    id: str
    delta: str


@dataclasses.dataclass(slots=True, kw_only=True)
class ReasoningEndEventPayload:
    type: Literal["reasoning-end"] = dataclasses.field(
        init=False, default="reasoning-end"
    )
    id: str


@dataclasses.dataclass(slots=True, kw_only=True)
class ToolInputStartEventPayload:
    type: Literal["tool-input-start"] = dataclasses.field(
        init=False, default="tool-input-start"
    )
    toolCallId: str
    toolName: str


@dataclasses.dataclass(slots=True, kw_only=True)
class ToolInputDeltaEventPayload:
    type: Literal["tool-input-delta"] = dataclasses.field(
        init=False, default="tool-input-delta"
    )
    toolCallId: str
    inputTextDelta: str


@dataclasses.dataclass(slots=True, kw_only=True)
class ToolInputAvailableEventPayload:
    type: Literal["tool-input-available"] = dataclasses.field(
        init=False, default="tool-input-available"
    )
    toolCallId: str
    toolName: str
    input: Any

    def __post_init__(self) -> None:
        if isinstance(self.input, dict):
            self.input = strip_proxy_tool_metadata(self.input)


@dataclasses.dataclass(slots=True, kw_only=True)
class ToolOutputAvailableEventPayload:
    type: Literal["tool-output-available"] = dataclasses.field(
        init=False, default="tool-output-available"
    )
    toolCallId: str
    output: Any


@dataclasses.dataclass(slots=True, kw_only=True)
class CompactionDataPayload:
    phase: Literal["started", "completed", "failed"]
    pre_tokens: int | None = None


@dataclasses.dataclass(slots=True, kw_only=True)
class DataEventPayload:
    type: str
    data: Any

    def __post_init__(self) -> None:
        if not self.type.startswith("data-"):
            msg = "Data event types must start with 'data-'"
            raise ValueError(msg)


@dataclasses.dataclass(slots=True, kw_only=True)
class ErrorEventPayload:
    type: Literal["error"] = dataclasses.field(init=False, default="error")
    errorText: str


VercelSSEPayload = (
    StartEventPayload
    | FinishEventPayload
    | TextStartEventPayload
    | TextDeltaEventPayload
    | TextEndEventPayload
    | ReasoningStartEventPayload
    | ReasoningDeltaEventPayload
    | ReasoningEndEventPayload
    | ToolInputStartEventPayload
    | ToolInputDeltaEventPayload
    | ToolInputAvailableEventPayload
    | ToolOutputAvailableEventPayload
    | DataEventPayload
    | ErrorEventPayload
)


def format_sse(data: VercelSSEPayload) -> str:
    """Formats a dictionary into a Server-Sent Event string."""
    return f"data: {to_json(data).decode()}\n\n"


@dataclasses.dataclass
class _PartState:
    part_id: str
    part_type: Literal["text", "reasoning", "tool"]
    tool_call: ToolCallPart | None = None
    open: bool = True


@dataclasses.dataclass
class VercelStreamContext:
    """State machine that converts pydantic-ai events into AI SDK SSE frames.

    The context keeps a part registry keyed by the provider's part index so that
    text, reasoning, and tool blocks can stream concurrently without stepping on
    each other's identifiers. Each entry records the synthetic Vercel message ID,
    the part kind, and any active tool invocation metadata so we can emit
    consistent start/delta/end sequences required by the Vercel protocol.
    """

    message_id: str
    # Active parts keyed by event index -> maintains per-part lifecycle state.
    part_states: dict[int, _PartState] = dataclasses.field(default_factory=dict)
    tool_finished: dict[str, bool] = dataclasses.field(default_factory=dict)
    tool_input_emitted: dict[str, bool] = dataclasses.field(default_factory=dict)
    tool_index: dict[str, int] = dataclasses.field(default_factory=dict)
    pending_data_events: list[DataEventPayload] = dataclasses.field(
        default_factory=list
    )
    # Cache approval data for continuation reconstruction
    approval_tool_name: dict[str, str] = dataclasses.field(default_factory=dict)
    approval_input: dict[str, Any] = dataclasses.field(default_factory=dict)

    def _create_part_state(
        self,
        index: int,
        part_type: Literal["text", "reasoning", "tool"],
        tool_call: ToolCallPart | None = None,
    ) -> _PartState:
        """Register a fresh part and return its tracking record."""
        part_id = f"msg_{uuid.uuid4().hex}"
        state = _PartState(part_id=part_id, part_type=part_type, tool_call=tool_call)
        self.part_states[index] = state
        if tool_call is not None:
            self.tool_index[tool_call.tool_call_id] = index
        return state

    def enqueue_data_event(self, payload: DataEventPayload) -> None:
        """Stage a data payload (e.g. approvals) to stream before tool parts."""
        self.pending_data_events.append(payload)

    def flush_data_events(self) -> list[DataEventPayload]:
        """Return and clear any staged data payloads."""
        if not self.pending_data_events:
            return []
        events = self.pending_data_events.copy()
        self.pending_data_events.clear()
        return events

    def _finalize_part(self, index: int) -> list[VercelSSEPayload]:
        """Close a part and emit any ending SSE frames that are still pending."""
        state = self.part_states.pop(index, None)
        if state is None or not state.open:
            return []

        events: list[VercelSSEPayload] = []
        if state.part_type == "text":
            events.append(TextEndEventPayload(id=state.part_id))
        elif state.part_type == "reasoning":
            events.append(ReasoningEndEventPayload(id=state.part_id))
        elif state.part_type == "tool" and state.tool_call is not None:
            tool_call_id = state.tool_call.tool_call_id
            if not self.tool_input_emitted.get(tool_call_id, False):
                events.append(
                    ToolInputAvailableEventPayload(
                        toolCallId=tool_call_id,
                        toolName=state.tool_call.tool_name,
                        input=state.tool_call.args_as_dict(),
                    )
                )
                self.tool_input_emitted[tool_call_id] = True
            self.tool_index.pop(tool_call_id, None)

        state.open = False
        return events

    def collect_current_part_end_events(
        self, index: int | None = None
    ) -> list[VercelSSEPayload]:
        """Generate SSE frames required to close active parts."""
        if index is not None:
            return self._finalize_part(index)

        events: list[VercelSSEPayload] = []
        for part_index in list(self.part_states.keys()):
            events.extend(self._finalize_part(part_index))
        return events

    async def handle_event(
        self, event: UnifiedStreamEvent
    ) -> AsyncIterator[VercelSSEPayload]:
        for data_event in self.flush_data_events():
            yield data_event
        async for payload in self._handle_unified_event(event):
            yield payload

    async def _handle_unified_event(
        self, event: UnifiedStreamEvent
    ) -> AsyncIterator[VercelSSEPayload]:
        """Processes a unified stream event and yields Vercel SDK SSE events."""
        match event.type:
            case StreamEventType.TEXT_START:
                # Close any existing stream for this index
                if event.part_id is not None:
                    for message in self.collect_current_part_end_events(
                        index=event.part_id
                    ):
                        yield message
                state = self._create_part_state(event.part_id or 0, "text")
                yield TextStartEventPayload(id=state.part_id)
                if event.text:
                    yield TextDeltaEventPayload(id=state.part_id, delta=event.text)

            case StreamEventType.TEXT_DELTA:
                if event.part_id is not None:
                    state = self.part_states.get(event.part_id)
                    if state is None:
                        logger.warning(
                            "Received delta for unknown part index",
                            index=event.part_id,
                        )
                    elif event.text:
                        yield TextDeltaEventPayload(id=state.part_id, delta=event.text)

            case StreamEventType.TEXT_STOP:
                if event.part_id is not None:
                    for message in self._finalize_part(event.part_id):
                        yield message

            case StreamEventType.THINKING_START:
                if event.part_id is not None:
                    for message in self.collect_current_part_end_events(
                        index=event.part_id
                    ):
                        yield message
                state = self._create_part_state(event.part_id or 0, "reasoning")
                yield ReasoningStartEventPayload(id=state.part_id)
                if event.thinking:
                    yield ReasoningDeltaEventPayload(
                        id=state.part_id, delta=event.thinking
                    )

            case StreamEventType.THINKING_DELTA:
                if event.part_id is not None:
                    state = self.part_states.get(event.part_id)
                    if state and event.thinking:
                        yield ReasoningDeltaEventPayload(
                            id=state.part_id, delta=event.thinking
                        )

            case StreamEventType.THINKING_STOP:
                if event.part_id is not None:
                    for message in self._finalize_part(event.part_id):
                        yield message

            case StreamEventType.TOOL_CALL_START:
                if event.part_id is not None:
                    for message in self.collect_current_part_end_events(
                        index=event.part_id
                    ):
                        yield message
                # Create a synthetic ToolCallPart for state tracking
                tool_call_id = event.tool_call_id or str(uuid.uuid4())
                tool_name = event.tool_name or "unknown"
                tool_call = ToolCallPart(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=strip_proxy_tool_metadata(event.tool_input or {}),
                )
                state = self._create_part_state(
                    event.part_id or 0, "tool", tool_call=tool_call
                )
                self.tool_finished.pop(tool_call_id, None)
                self.tool_input_emitted[tool_call_id] = False
                yield ToolInputStartEventPayload(
                    toolCallId=tool_call_id, toolName=tool_name
                )

            case StreamEventType.TOOL_CALL_DELTA:
                if event.part_id is not None:
                    state = self.part_states.get(event.part_id)
                    if state and state.tool_call and event.text:
                        yield ToolInputDeltaEventPayload(
                            toolCallId=state.tool_call.tool_call_id,
                            inputTextDelta=event.text,
                        )

            case StreamEventType.TOOL_CALL_STOP:
                if event.part_id is not None:
                    state = self.part_states.get(event.part_id)
                    if state and state.tool_call:
                        tool_call_id = state.tool_call.tool_call_id
                        if not self.tool_input_emitted.get(tool_call_id, False):
                            # Emit final tool input
                            yield ToolInputAvailableEventPayload(
                                toolCallId=tool_call_id,
                                toolName=event.tool_name or state.tool_call.tool_name,
                                input=event.tool_input
                                or state.tool_call.args_as_dict(),
                            )
                            self.tool_input_emitted[tool_call_id] = True
                    for message in self._finalize_part(event.part_id):
                        yield message

            case StreamEventType.TOOL_RESULT:
                tool_call_id = event.tool_call_id or "unknown"
                has_known_tool_input = self.tool_input_emitted.get(tool_call_id, False)
                has_cached_tool_metadata = tool_call_id in self.approval_tool_name

                # Close any open part for this tool
                if tool_call_id in self.tool_index:
                    index = self.tool_index[tool_call_id]
                    for message in self.collect_current_part_end_events(index=index):
                        yield message
                    has_known_tool_input = True

                if (
                    not has_known_tool_input
                    and not has_cached_tool_metadata
                    and event.tool_name is None
                ):
                    logger.debug(
                        "Skipping uncorrelated tool result without tool metadata",
                        tool_call_id=tool_call_id,
                    )
                    return

                # Ensure input-available before output
                if not self.tool_input_emitted.get(tool_call_id, False):
                    tool_name = (
                        self.approval_tool_name.get(tool_call_id)
                        or event.tool_name
                        or "tool"
                    )
                    yield ToolInputAvailableEventPayload(
                        toolCallId=tool_call_id,
                        toolName=tool_name,
                        input=self.approval_input.get(tool_call_id, {}),
                    )
                    self.tool_input_emitted[tool_call_id] = True

                self.tool_finished[tool_call_id] = True
                self.tool_input_emitted.pop(tool_call_id, None)

                # Check for structured error format from MCP proxy
                # (workaround for Claude Agent SDK not propagating is_error)
                structured_error = _extract_structured_error(event.tool_output)

                if event.is_error or structured_error:
                    error_text = (
                        structured_error
                        or event.tool_output
                        or event.error
                        or "Unknown error"
                    )
                    yield ToolOutputAvailableEventPayload(
                        toolCallId=tool_call_id,
                        output={"errorText": error_text},
                    )
                else:
                    yield ToolOutputAvailableEventPayload(
                        toolCallId=tool_call_id,
                        output=event.tool_output,
                    )

            case StreamEventType.COMPACTION:
                metadata = event.metadata or {}
                payload = CompactionDataPayload(
                    phase=metadata["phase"],
                    pre_tokens=metadata.get("pre_tokens"),
                )
                yield DataEventPayload(
                    type=COMPACTION_DATA_PART_TYPE,
                    data=payload,
                )

            case StreamEventType.ERROR:
                yield ErrorEventPayload(errorText=event.error or "Unknown error")

            case StreamEventType.APPROVAL_REQUEST:
                # Unified approval request from any harness (pydantic-ai or claude)
                if event.approval_items:
                    for item in event.approval_items:
                        sanitized_input = strip_proxy_tool_metadata(item.input)
                        # Cache tool data for UI reconstruction on continuation
                        self.approval_tool_name[item.id] = item.name
                        self.approval_input[item.id] = sanitized_input

                        # Finalize any open tool parts so UI shows input-available
                        if item.id in self.tool_index:
                            index = self.tool_index[item.id]
                            for end_evt in self.collect_current_part_end_events(
                                index=index
                            ):
                                yield end_evt

                    # Emit data-approval-request event for frontend
                    yield DataEventPayload(
                        type=APPROVAL_DATA_PART_TYPE,
                        data=[
                            {
                                "tool_call_id": item.id,
                                "tool_name": item.name,
                                "args": strip_proxy_tool_metadata(item.input),
                            }
                            for item in event.approval_items
                        ],
                    )

            case (
                StreamEventType.MESSAGE_START
                | StreamEventType.MESSAGE_STOP
                | StreamEventType.DONE
            ):
                # Lifecycle events - no Vercel SSE emission needed
                pass

            case _:
                logger.warning("Unhandled unified event type", event_type=event.type)


@dataclasses.dataclass
class MutableToolPart:
    type: str
    tool_call_id: str
    state: Literal["input-available", "output-available", "output-error"]
    input: Any
    output: Any | None = None
    error_text: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.input, dict):
            self.input = strip_proxy_tool_metadata(self.input)

    def set_result(
        self,
        content: Any,
        structured_error: str | None = None,
        *,
        is_error: bool = False,
    ) -> None:
        """Set the tool result, detecting structured errors.

        Args:
            content: The tool output content.
            structured_error: Pre-extracted error from structured format.
            is_error: Whether the underlying SDK flagged this as an error.
        """
        error_text = structured_error or (
            (content if isinstance(content, str) else str(content))
            if is_error
            else None
        )
        if error_text:
            self.state = "output-error"
            self.output = None
            self.error_text = error_text
        else:
            self.state = "output-available"
            self.output = content
            self.error_text = None

    @classmethod
    def with_result(
        cls,
        type: str,
        tool_call_id: str,
        input: Any,
        content: Any,
        structured_error: str | None = None,
        *,
        is_error: bool = False,
    ) -> MutableToolPart:
        """Create a MutableToolPart with result already set."""
        error_text = structured_error or (
            (content if isinstance(content, str) else str(content))
            if is_error
            else None
        )
        if error_text:
            return cls(
                type=type,
                tool_call_id=tool_call_id,
                state="output-error",
                input=input,
                error_text=error_text,
            )
        return cls(
            type=type,
            tool_call_id=tool_call_id,
            state="output-available",
            input=input,
            output=content,
        )

    def to_ui_part(self) -> ToolUIPart:
        if self.state == "input-available":
            return ToolUIPartInputAvailable(
                type=self.type,
                toolCallId=self.tool_call_id,
                state="input-available",
                input=self.input,
            )
        if self.state == "output-available":
            return ToolUIPartOutputAvailable(
                type=self.type,
                toolCallId=self.tool_call_id,
                state="output-available",
                input=self.input,
                output=self.output,
            )
        return ToolUIPartOutputError(
            type=self.type,
            toolCallId=self.tool_call_id,
            state="output-error",
            input=self.input,
            errorText=self.error_text or "",
        )


@dataclasses.dataclass
class MutableMessage:
    id: str
    role: Literal["system", "user", "assistant"]
    parts: list[MutableToolPart | UIMessagePart]


UIMessagesTA: pydantic.TypeAdapter[list[UIMessage]] = pydantic.TypeAdapter(
    list[UIMessage]
)


def _extract_approval_payload_from_message(
    message: UnifiedMessage,
) -> list[ToolCallPart] | None:
    approvals: list[ToolCallPart] = []
    if isinstance(message, AssistantMessage):
        content = message.content
        if not isinstance(content, list) or not content:
            return None

        # Check for approval header as first text block
        first_block = content[0]
        if not isinstance(first_block, TextBlock):
            return None
        if first_block.text != APPROVAL_REQUEST_HEADER:
            return None

        # Extract tool calls from remaining blocks
        for block in content[1:]:
            if isinstance(block, ToolUseBlock):
                approvals.append(
                    ToolCallPart(
                        tool_name=block.name,
                        tool_call_id=block.id,
                        args=strip_proxy_tool_metadata(block.input or {}),
                    )
                )
        return approvals if approvals else None

    return None


def _iter_message_parts(
    message: UnifiedMessage,
) -> Iterator[tuple[str, Any]]:
    if isinstance(message, AssistantMessage | UserMessage):
        content = message.content
        if isinstance(content, str):
            yield ("TextBlock", TextBlock(text=content))
        elif isinstance(content, list):
            for block in content:
                yield (type(block).__name__, block)


def _is_internal_interrupt_message(chat_message: ChatMessage) -> bool:
    """Check if message is an internal Claude Code SDK interrupt state.

    These are intermediate messages generated by the Claude Code SDK during
    tool interruption/approval flow that should not appear in the chat timeline:
    - tool_result with is_error=True and "doesn't want to take this action" content
    - Text messages with "[Request interrupted by user for tool use]"
    - Synthetic assistant messages (model="<synthetic>") with "No response requested."

    Args:
        chat_message: ChatMessage to check

    Returns:
        True if this is an internal interrupt message that should be filtered
    """
    message_data = chat_message.message
    if message_data is None:
        return False

    content: str | list[Any] | None = None
    if isinstance(message_data, AssistantMessage | UserMessage):
        content = message_data.content

    if content is None:
        return False

    # Check string content patterns (Claude SDK messages)
    if isinstance(content, str):
        if "[Request interrupted by user" in content:
            return True
        if content == "No response requested.":
            return True

    # Check list content patterns (Claude SDK messages with content blocks)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                # Cast to dict[str, Any] for type checker - isinstance(part, dict)
                # only narrows to dict[Unknown, Unknown], but we know Claude SDK
                # content blocks are always dict[str, Any] at runtime
                part_dict = cast(dict[str, Any], part)
                part_type = part_dict.get("type")
                if part_type == "tool_result" and part_dict.get("is_error"):
                    text = str(part_dict.get("content", ""))
                    if "doesn't want to take this action" in text:
                        return True
                if part_type == "text":
                    text = part_dict.get("text", "")
                    if "[Request interrupted by user" in text:
                        return True
                    if text == "No response requested.":
                        return True
            # Handle typed Claude SDK blocks
            elif isinstance(part, TextBlock):
                if "[Request interrupted by user" in part.text:
                    return True
                if part.text == "No response requested.":
                    return True

    return False


def convert_chat_messages_to_ui(
    messages: list[ChatMessage],
) -> list[UIMessage]:
    """Convert persisted ChatMessage format to Vercel UIMessage format.

    Args:
        messages: List of ChatMessage objects from the database

    Returns:
        List of UIMessage objects for the Vercel AI SDK
    """
    mutable_messages: list[MutableMessage] = []
    tool_entries: dict[str, MutableToolPart] = {}

    for chat_message in messages:
        # Handle compaction status badges from DB (kind=COMPACTION)
        # These show when a conversation was compacted
        if chat_message.kind == MessageKind.COMPACTION and chat_message.compaction:
            compaction_data = chat_message.compaction
            # Create a system message with the compaction data part
            mutable_message = MutableMessage(
                id=chat_message.id,
                role="system",
                parts=[
                    DataUIPart(type=COMPACTION_DATA_PART_TYPE, data=compaction_data)
                ],
            )
            mutable_messages.append(mutable_message)
            continue

        # Handle approval request bubbles from DB (kind=APPROVAL_REQUEST)
        # These are inserted by list_messages() when loading session history
        if chat_message.kind == MessageKind.APPROVAL_REQUEST and chat_message.approval:
            approval = chat_message.approval
            if approval.status != ApprovalStatus.PENDING:
                continue
            # Create an assistant message with the approval data part
            # Normalize tool name for display
            tool_name = normalize_mcp_tool_name(approval.tool_name)
            approval_data = {
                "tool_call_id": approval.tool_call_id,
                "tool_name": tool_name,
                "args": approval.tool_call_args or {},
            }
            mutable_message = MutableMessage(
                id=chat_message.id,
                role="assistant",
                parts=[DataUIPart(type=APPROVAL_DATA_PART_TYPE, data=[approval_data])],
            )
            mutable_messages.append(mutable_message)
            continue

        # Skip approval decision bubbles (they don't render in UI)
        if chat_message.kind == MessageKind.APPROVAL_DECISION:
            continue

        message_data = chat_message.message

        # Skip internal interrupt messages from Claude Code SDK
        if _is_internal_interrupt_message(chat_message):
            continue

        # Extract message data from the ChatMessage schema
        message_id = chat_message.id

        # Determine role based on message type
        role: Literal["system", "user", "assistant"]
        if isinstance(message_data, AssistantMessage):
            role = "assistant"
        elif isinstance(message_data, UserMessage):
            role = "user"
        else:
            continue

        # Type narrowing: after the isinstance checks above, message_data is UnifiedMessage
        # (the else branch continues, so None is excluded here)
        assert message_data is not None

        mutable_message = MutableMessage(id=message_id, role=role, parts=[])
        approval_payload = _extract_approval_payload_from_message(message_data)

        for part_type, part in _iter_message_parts(message_data):
            if approval_payload:
                if part_type == "TextBlock" and part.text == APPROVAL_REQUEST_HEADER:
                    continue

            match part_type:
                case "TextBlock":
                    mutable_message.parts.append(
                        TextUIPart(type="text", text=part.text, state="done")
                    )

                case "ThinkingBlock":
                    mutable_message.parts.append(
                        ReasoningUIPart(
                            type="reasoning", text=part.thinking, state="done"
                        )
                    )

                case "ToolUseBlock":
                    if approval_payload:
                        continue
                    # Extract underlying tool name for execute_tool wrapper
                    tool_name = part.name
                    tool_input = part.input or {}
                    if part.name in {
                        "mcp__tracecat-registry__execute_tool",
                        "mcp__tracecat_registry__execute_tool",
                    } and isinstance(tool_input, dict):
                        tool_name = tool_input.get("tool_name", part.name)
                        tool_input = tool_input.get("args", tool_input)
                    # Normalize MCP registry prefix
                    tool_name = normalize_mcp_tool_name(tool_name)
                    tool_part = MutableToolPart(
                        type=f"tool-{tool_name}",
                        tool_call_id=part.id,
                        state="input-available",
                        input=tool_input,
                    )
                    mutable_message.parts.append(tool_part)
                    tool_entries[part.id] = tool_part

                case "ToolResultBlock":
                    # Check for structured error format from MCP proxy
                    structured_error = _extract_structured_error(part.content)

                    existing = tool_entries.get(part.tool_use_id)
                    if existing is not None:
                        existing.set_result(
                            part.content, structured_error, is_error=part.is_error
                        )
                    else:
                        # Create fallback part when no existing tool entry is found
                        tool_part = MutableToolPart.with_result(
                            type="tool-unknown",
                            tool_call_id=part.tool_use_id,
                            input={},
                            content=part.content,
                            structured_error=structured_error,
                            is_error=part.is_error,
                        )
                        mutable_message.parts.append(tool_part)
                        tool_entries[part.tool_use_id] = tool_part

                case _:
                    # Skip unknown part types (SystemPromptPart, UserPromptPart, etc.)
                    pass

        if approval_payload:
            mutable_message.parts.append(
                DataUIPart(type=APPROVAL_DATA_PART_TYPE, data=approval_payload)
            )

        if mutable_message.parts:
            mutable_messages.append(mutable_message)

    raw_messages: list[dict[str, Any]] = []
    for message in mutable_messages:
        parts: list[UIMessagePart] = []
        for part in message.parts:
            if isinstance(part, MutableToolPart):
                parts.append(part.to_ui_part())
            else:
                parts.append(part)
        raw_messages.append(
            {
                "id": message.id,
                "role": message.role,
                "parts": parts,
            }
        )

    return UIMessagesTA.validate_python(raw_messages)


async def sse_vercel(events: AsyncIterable[StreamEvent]) -> AsyncIterable[str]:
    """Stream Redis events as Vercel AI SDK frames without persisting adapter output."""

    message_id = f"msg_{uuid.uuid4().hex}"
    context = VercelStreamContext(message_id=message_id)

    try:
        # 1. Start of the message stream
        yield format_sse(StartEventPayload(messageId=message_id))

        # 2. Process events from Redis stream
        async for stream_event in events:
            match stream_event:
                case StreamDelta(event=agent_event):
                    # Process agent stream events (PartStartEvent, PartDeltaEvent, etc.)
                    async for msg in context.handle_event(agent_event):
                        yield format_sse(msg)
                case StreamKeepAlive():
                    yield StreamKeepAlive.sse()
                case StreamError(error=error):
                    # Stream error - emit as text component
                    error_part_id = f"msg_{uuid.uuid4().hex}"
                    msg = StreamError.format(error)
                    yield format_sse(TextStartEventPayload(id=error_part_id))
                    yield format_sse(TextDeltaEventPayload(id=error_part_id, delta=msg))
                    yield format_sse(TextEndEventPayload(id=error_part_id))
                    yield format_sse(ErrorEventPayload(errorText=msg))
                case StreamEnd():
                    # End of stream marker from Redis
                    logger.debug("End-of-stream marker from Redis")
                    break

        # 3. Finalize any open parts at the end of the stream
        for message in context.collect_current_part_end_events():
            yield format_sse(message)

    except Exception as e:
        # 4. Handle errors
        logger.error("Error in Vercel SSE stream", error=str(e))
        yield format_sse(ErrorEventPayload(errorText=str(e)))
        raise e
    finally:
        # 5. Finish the message and terminate the stream
        logger.debug("Finishing Vercel SSE stream")
        yield format_sse(FinishEventPayload())
        yield "data: [DONE]\n\n"
