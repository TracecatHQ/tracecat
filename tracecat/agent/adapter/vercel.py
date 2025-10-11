"""Data models and adapter logic for the Vercel AI SDK. Taken from https://gist.github.com/183amir/ce45cf52f034b493fac0bb4b1838236a
Spec: https://sdk.vercel.ai/docs/concepts/stream-protocol#data-stream-protocol
Data models: https://github.com/vercel/ai/blob/b024298c/packages/ai/src/ui/ui-messages.ts
As of 18.09.2025 full implementation of this is being added to pydantic AI in https://github.com/pydantic/pydantic-ai/pull/2923
"""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NotRequired,
    TypedDict,
    TypeGuard,
)

import pydantic
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    AgentStreamEvent,
    AudioUrl,
    BinaryContent,
    BuiltinToolCallPart,
    BuiltinToolReturnPart,
    DocumentUrl,
    FunctionToolResultEvent,
    ImageUrl,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ModelResponsePart,
    MultiModalContent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
    VideoUrl,
)

from tracecat.agent.stream.events import (
    StreamDelta,
    StreamEnd,
    StreamError,
    StreamEvent,
    StreamKeepAlive,
    StreamMessage,
)
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.chat.models import ChatMessage

# Using a type alias for ProviderMetadata since its structure is not defined.
ProviderMetadata = dict[str, dict[str, Any]]

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


def _get_tool_name(part: ToolUIPart | DynamicToolUIPart) -> str:
    """Extracts the tool name from a tool UI part."""
    # DynamicToolUIPart has toolName attribute
    if is_dynamic_tool_part(part):
        return part["toolName"]

    # ToolUIPart has the tool name in the type field (e.g., "tool-search")
    # Extract the name after "tool-" prefix
    part_type = part.get("type") if isinstance(part, dict) else None
    if isinstance(part_type, str) and part_type.startswith("tool-"):
        return part_type.split("-", 1)[1]
    raise ValueError("Invalid tool UI part without a tool name")


def _convert_file_part(part: FileUIPart) -> MultiModalContent:
    """Converts a FileUIPart into a pydantic-ai MultiModalContent object."""
    # Check for Data URLs (e.g., "data:image/png;base64,iVBORw...")
    data_url_match = re.match(
        r"data:(?P<media_type>[^;]+);base64,(?P<data>.+)", part["url"]
    )
    if data_url_match:
        media_type = data_url_match.group("media_type")
        b64_data = data_url_match.group("data")
        binary_data = base64.b64decode(b64_data)
        return BinaryContent(data=binary_data, media_type=media_type)

    # Handle regular URLs
    media_type = part["mediaType"]
    if media_type.startswith("image/"):
        return ImageUrl(url=part["url"], media_type=media_type)
    elif media_type.startswith("audio/"):
        return AudioUrl(url=part["url"], media_type=media_type)
    elif media_type.startswith("video/"):
        return VideoUrl(url=part["url"], media_type=media_type)
    else:
        # Default to DocumentUrl for other types like application/pdf, text/plain etc.
        return DocumentUrl(url=part["url"], media_type=media_type)


def convert_ui_message(
    ui_message: UIMessage,
) -> list[ModelRequest | ModelResponse]:
    """
    Converts a single UIMessage object into a list of pydantic-ai
    ModelRequest or ModelResponse objects.
    """
    if ui_message.role == "system":
        content = "\n".join(
            part["text"] for part in ui_message.parts if is_text_ui_part(part)
        )
        if not content:
            return []
        return [ModelRequest(parts=[SystemPromptPart(content=content)])]

    if ui_message.role == "user":
        user_content_parts: list[UserContent] = []
        for part in ui_message.parts:
            if is_text_ui_part(part):
                user_content_parts.append(part["text"])
            elif is_file_ui_part(part):
                user_content_parts.append(_convert_file_part(part))
            # Other part types are ignored for user roles as they are not
            # standard inputs.

        if not user_content_parts:
            return []

        user_prompt_content: str | list[UserContent] = (
            user_content_parts[0]
            if len(user_content_parts) == 1 and isinstance(user_content_parts[0], str)
            else user_content_parts
        )
        return [ModelRequest(parts=[UserPromptPart(content=user_prompt_content)])]

    if ui_message.role == "assistant":
        result_messages: list[ModelRequest | ModelResponse] = []
        model_response_parts: list[TextPart | ThinkingPart | ToolCallPart] = []
        tool_return_request_parts: list[ToolReturnPart | RetryPromptPart] = []

        for part in ui_message.parts:
            # Type narrowing for the union
            if is_text_ui_part(part):
                model_response_parts.append(TextPart(content=part["text"]))
            elif is_reasoning_ui_part(part):
                model_response_parts.append(ThinkingPart(content=part["text"]))
            elif is_dynamic_tool_part(part) or is_tool_part(part):
                # Now we have proper type narrowing for tool parts
                tool_name = _get_tool_name(part)
                tool_call_id = part["toolCallId"]
                state = part.get("state") if isinstance(part, dict) else None
                input_payload = part.get("input") if isinstance(part, dict) else None

                # The model's decision to call a tool is part of its response.
                if state in (
                    "input-available",
                    "output-available",
                    "output-error",
                ):
                    model_response_parts.append(
                        ToolCallPart(
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                            args=input_payload or {},
                        )
                    )

                # The result of the tool call is sent back in a new request.
                if state == "output-available":
                    tool_return_request_parts.append(
                        ToolReturnPart(
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                            content=part.get("output"),
                        )
                    )
                elif state == "output-error":
                    error_text = (
                        part.get("errorText") if isinstance(part, dict) else None
                    )
                    if error_text:
                        tool_return_request_parts.append(
                            RetryPromptPart(
                                tool_name=tool_name,
                                tool_call_id=tool_call_id,
                                content=error_text,
                            )
                        )

        if model_response_parts:
            result_messages.append(ModelResponse(parts=model_response_parts))

        if tool_return_request_parts:
            result_messages.append(ModelRequest(parts=tool_return_request_parts))

        return result_messages

    return []


def convert_ui_messages(
    ui_messages: list[UIMessage],
) -> list[ModelRequest | ModelResponse]:
    """
    Converts a list of UIMessage objects into a flattened list of pydantic-ai
    ModelRequest or ModelResponse objects.

    Args:
        ui_messages: The list of UIMessage objects to convert.

    Returns:
        A flattened list of corresponding pydantic-ai message objects.
    """
    all_model_messages: list[ModelRequest | ModelResponse] = []
    for ui_message in ui_messages:
        all_model_messages.extend(convert_ui_message(ui_message))
    return all_model_messages


# ==============================================================================
# 8. Vercel AI SDK Data Stream Protocol Adapter
# ==============================================================================
def format_sse(data: dict[str, Any]) -> str:
    """Formats a dictionary into a Server-Sent Event string."""
    json_data = json.dumps(data, separators=(",", ":"))
    return f"data: {json_data}\n\n"


@dataclasses.dataclass
class VercelStreamContext:
    """Manages state for a Vercel AI SDK data stream."""

    message_id: str
    current_part_id: str | None = None
    current_part_type: Literal["text", "reasoning", "tool"] | None = None
    current_tool_call: ToolCallPart | None = None
    tool_finished: dict[str, bool] = dataclasses.field(default_factory=dict)
    tool_input_emitted: dict[str, bool] = dataclasses.field(default_factory=dict)

    def new_part(self) -> str:
        """Generates a new unique ID for a stream part."""
        self.current_part_id = f"msg_{uuid.uuid4().hex}"
        return self.current_part_id

    def _reset_current_part(self) -> None:
        """Clear the currently tracked part metadata."""
        self.current_part_id = None
        self.current_part_type = None
        self.current_tool_call = None

    def collect_current_part_end_events(self) -> list[str]:
        """Generate SSE frames required to close the current part, if any."""
        if self.current_part_type is None:
            return []

        events: list[str] = []
        if self.current_part_type == "text" and self.current_part_id:
            events.append(format_sse({"type": "text-end", "id": self.current_part_id}))
        elif self.current_part_type == "reasoning" and self.current_part_id:
            events.append(
                format_sse({"type": "reasoning-end", "id": self.current_part_id})
            )
        elif self.current_part_type == "tool" and self.current_tool_call:
            tool_call_id = self.current_tool_call.tool_call_id
            if not self.tool_input_emitted.get(tool_call_id, False):
                events.append(
                    format_sse(
                        {
                            "type": "tool-input-available",
                            "toolCallId": tool_call_id,
                            "toolName": self.current_tool_call.tool_name,
                            "input": self.current_tool_call.args_as_dict(),
                        }
                    )
                )
                self.tool_input_emitted[tool_call_id] = True

        self._reset_current_part()
        return events

    async def handle_event(self, event: AgentStreamEvent) -> AsyncIterator[str]:
        """Processes a pydantic-ai agent event and yields Vercel SDK SSE events."""
        # End the previous part if a new one is starting
        if isinstance(event, PartStartEvent):
            for message in self.collect_current_part_end_events():
                yield message

        # Handle Model Response Stream Events
        if isinstance(event, PartStartEvent):
            self.new_part()
            part = event.part
            if isinstance(part, TextPart):
                self.current_part_type = "text"
                yield format_sse({"type": "text-start", "id": self.current_part_id})
                if part.content:
                    yield format_sse(
                        {
                            "type": "text-delta",
                            "id": self.current_part_id,
                            "delta": part.content,
                        }
                    )
            elif isinstance(part, ThinkingPart):
                self.current_part_type = "reasoning"
                yield format_sse(
                    {"type": "reasoning-start", "id": self.current_part_id}
                )
                if part.content:
                    yield format_sse(
                        {
                            "type": "reasoning-delta",
                            "id": self.current_part_id,
                            "delta": part.content,
                        }
                    )
            elif isinstance(part, ToolCallPart):
                self.current_part_type = "tool"
                self.current_tool_call = part
                self.tool_finished.pop(part.tool_call_id, None)
                self.tool_input_emitted[part.tool_call_id] = False
                yield format_sse(
                    {
                        "type": "tool-input-start",
                        "toolCallId": part.tool_call_id,
                        "toolName": part.tool_name,
                    }
                )
                if part.args:
                    yield format_sse(
                        {
                            "type": "tool-input-delta",
                            "toolCallId": part.tool_call_id,
                            "inputTextDelta": part.args_as_json_str(),
                        }
                    )
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and self.current_part_id:
                yield format_sse(
                    {
                        "type": "text-delta",
                        "id": self.current_part_id,
                        "delta": delta.content_delta,
                    }
                )
            elif isinstance(delta, ThinkingPartDelta) and self.current_part_id:
                if delta.content_delta:
                    yield format_sse(
                        {
                            "type": "reasoning-delta",
                            "id": self.current_part_id,
                            "delta": delta.content_delta,
                        }
                    )
            elif isinstance(delta, ToolCallPartDelta) and self.current_tool_call:
                try:
                    updated_tool_call = delta.apply(self.current_tool_call)
                except (UnexpectedModelBehavior, ValueError):
                    logger.exception(
                        "Failed to apply tool call delta",
                        tool_call_id=self.current_tool_call.tool_call_id,
                    )
                else:
                    self.current_tool_call = updated_tool_call
                    if delta.args_delta:
                        delta_str = (
                            delta.args_delta
                            if isinstance(delta.args_delta, str)
                            else json.dumps(delta.args_delta)
                        )
                        yield format_sse(
                            {
                                "type": "tool-input-delta",
                                "toolCallId": self.current_tool_call.tool_call_id,
                                "inputTextDelta": delta_str,
                            }
                        )

        # Handle Tool Call and Result Events
        elif isinstance(event, FunctionToolResultEvent):
            for message in self.collect_current_part_end_events():
                yield message
            if isinstance(event.result, ToolReturnPart):
                tool_call_id = event.result.tool_call_id
                self.tool_finished[tool_call_id] = True
                self.tool_input_emitted.pop(tool_call_id, None)
                yield format_sse(
                    {
                        "type": "tool-output-available",
                        "toolCallId": tool_call_id,
                        "output": event.result.model_response_str(),
                    }
                )
            elif isinstance(event.result, RetryPromptPart):
                if event.result.tool_call_id:
                    tool_call_id = event.result.tool_call_id
                    self.tool_finished[tool_call_id] = True
                    self.tool_input_emitted.pop(tool_call_id, None)
                yield format_sse(
                    {"type": "error", "errorText": event.result.model_response()}
                )


# ==============================================================================
# 9. Convert Persisted ModelMessage to UIMessage
# ==============================================================================


def _convert_model_message_part_to_ui_part(
    part: ModelResponsePart | ModelRequestPart,
) -> UIMessagePart | None:
    """Convert a single ModelMessage part to a UIMessage part.

    Args:
        part: A pydantic-ai message part (TextPart, ToolCallPart, etc.)

    Returns:
        Converted UIMessagePart or None if conversion not supported
    """
    # Use match-case for structural pattern matching with proper type narrowing
    match part:
        case TextPart(content=str(content)):
            return TextUIPart(type="text", text=content, state="done")

        case ThinkingPart(content=str(content)):
            return ReasoningUIPart(type="reasoning", text=content, state="done")

        case UserPromptPart(content=content):
            match content:
                case str(text):
                    pass
                case list(items):
                    text = "\n".join(
                        item if isinstance(item, str) else json.dumps(item)
                        for item in items
                    )
                case _:
                    text = json.dumps(content)
            return TextUIPart(type="text", text=text, state="done")

        case SystemPromptPart(content=str(content)):
            return TextUIPart(type="text", text=content, state="done")

    return None


@dataclasses.dataclass
class MutableToolPart:
    type: str
    tool_call_id: str
    state: Literal["input-available", "output-available", "output-error"]
    input: Any
    output: Any | None = None
    error_text: str | None = None

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


def convert_model_messages_to_ui(
    messages: list[ChatMessage],
) -> list[UIMessage]:
    """Convert persisted ModelMessage format to Vercel UIMessage format.

    Args:
        messages: List of ChatMessage objects from the database

    Returns:
        List of UIMessage objects for the Vercel AI SDK
    """
    mutable_messages: list[MutableMessage] = []
    tool_entries: dict[str, MutableToolPart] = {}

    for chat_message in messages:
        # Extract message data from the ChatMessage schema
        message_id = chat_message.id
        message_data = chat_message.message

        # Determine role from message kind
        role: Literal["system", "user", "assistant"] = (
            "assistant" if message_data.kind == "response" else "user"
        )

        mutable_message = MutableMessage(id=message_id, role=role, parts=[])

        for part in message_data.parts:
            match part:
                case ToolCallPart(tool_name=tool_name, tool_call_id=tool_call_id):
                    tool_input = part.args_as_dict()
                    tool_part = MutableToolPart(
                        type=f"tool-{tool_name}",
                        tool_call_id=tool_call_id,
                        state="input-available",
                        input=tool_input,
                    )
                    mutable_message.parts.append(tool_part)
                    tool_entries[tool_call_id] = tool_part
                    continue

                case BuiltinToolCallPart(
                    tool_name=tool_name, tool_call_id=tool_call_id
                ):
                    tool_input = part.args_as_dict()
                    tool_part = MutableToolPart(
                        type=f"tool-{tool_name}",
                        tool_call_id=tool_call_id,
                        state="input-available",
                        input=tool_input,
                    )
                    mutable_message.parts.append(tool_part)
                    tool_entries[tool_call_id] = tool_part
                    continue

                case ToolReturnPart(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    content=content,
                ):
                    existing_part = tool_entries.get(tool_call_id)
                    input_payload = existing_part.input if existing_part else {}
                    if existing_part is not None:
                        existing_part.state = "output-available"
                        existing_part.output = content
                        existing_part.error_text = None
                    else:
                        tool_part = MutableToolPart(
                            type=f"tool-{tool_name}",
                            tool_call_id=tool_call_id,
                            state="output-available",
                            input=input_payload,
                            output=content,
                        )
                        mutable_message.parts.append(tool_part)
                        tool_entries[tool_call_id] = tool_part
                    continue

                case BuiltinToolReturnPart(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    content=content,
                ):
                    existing_part = tool_entries.get(tool_call_id)
                    input_payload = existing_part.input if existing_part else {}
                    if existing_part is not None:
                        existing_part.state = "output-available"
                        existing_part.output = content
                        existing_part.error_text = None
                    else:
                        tool_part = MutableToolPart(
                            type=f"tool-{tool_name}",
                            tool_call_id=tool_call_id,
                            state="output-available",
                            input=input_payload,
                            output=content,
                        )
                        mutable_message.parts.append(tool_part)
                        tool_entries[tool_call_id] = tool_part
                    continue

                case RetryPromptPart(
                    tool_name=str(tool_name),
                    tool_call_id=tool_call_id,
                    content=content,
                ):
                    existing_part = tool_entries.get(tool_call_id)
                    input_payload = existing_part.input if existing_part else {}
                    error_text = content if isinstance(content, str) else str(content)
                    if existing_part is not None:
                        existing_part.state = "output-error"
                        existing_part.output = None
                        existing_part.error_text = error_text
                    else:
                        tool_part = MutableToolPart(
                            type=f"tool-{tool_name}",
                            tool_call_id=tool_call_id,
                            state="output-error",
                            input=input_payload,
                            error_text=error_text,
                        )
                        mutable_message.parts.append(tool_part)
                        tool_entries[tool_call_id] = tool_part
                    continue

            converted_part = _convert_model_message_part_to_ui_part(part)
            if converted_part is not None:
                mutable_message.parts.append(converted_part)

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
    context = VercelStreamContext(message_id=message_id)  # type: ignore[call-arg]

    try:
        # 1. Start of the message stream
        yield format_sse({"type": "start", "messageId": message_id})

        # 2. Process events from Redis stream
        async for stream_event in events:
            match stream_event:
                case StreamDelta(event=agent_event):
                    # Process agent stream events (PartStartEvent, PartDeltaEvent, etc.)
                    async for msg in context.handle_event(agent_event):
                        yield msg
                case StreamMessage():
                    # Model messages don't need processing through handle_event
                    # They're just stored/logged
                    continue
                case StreamKeepAlive():
                    yield StreamKeepAlive.sse()
                case StreamError(error=error):
                    # Stream error - emit as text component
                    error_part_id = f"msg_{uuid.uuid4().hex}"
                    msg = StreamError.format(error)
                    yield format_sse({"type": "text-start", "id": error_part_id})
                    yield format_sse(
                        {"type": "text-delta", "id": error_part_id, "delta": msg}
                    )
                    yield format_sse({"type": "text-end", "id": error_part_id})
                    yield format_sse({"type": "error", "errorText": msg})
                    break
                case StreamEnd():
                    # End of stream marker from Redis
                    logger.debug("End-of-stream marker from Redis")
                    break

        # 3. Finalize any open parts at the end of the stream
        for message in context.collect_current_part_end_events():
            yield message

    except Exception as e:
        # 4. Handle errors
        logger.error("Error in Vercel SSE stream", error=str(e))
        yield format_sse({"type": "error", "errorText": str(e)})
        raise e
    finally:
        # 5. Finish the message and terminate the stream
        logger.debug("Finishing Vercel SSE stream")
        yield format_sse({"type": "finish"})
        yield "data: [DONE]\n\n"
