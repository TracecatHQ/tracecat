from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    Protocol,
    TypedDict,
    runtime_checkable,
)

import pydantic
from claude_agent_sdk.types import Message as ClaudeSDKMessage
from pydantic import Discriminator, TypeAdapter

from tracecat.agent.stream.types import ToolCallContent
from tracecat.chat.enums import MessageKind
from tracecat.config import TRACECAT__AGENT_MAX_RETRIES

if TYPE_CHECKING:
    from pydantic_ai import ModelResponse
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.tools import Tool as _PATool

    from tracecat.agent.stream.writers import StreamWriter
    from tracecat.chat.schemas import ChatMessage

    CustomToolList = list[_PATool[Any]]
else:
    # Runtime fallbacks for types only used in annotations
    ModelResponse = Any
    ModelMessage = Any
    CustomToolList = list[Any]


class MCPServerConfig(TypedDict):
    """Configuration for an MCP server."""

    url: str
    headers: dict[str, str]


class StreamKey(str):
    def __new__(
        cls,
        workspace_id: uuid.UUID | str,
        session_id: uuid.UUID | str,
        *,
        namespace: str = "agent",
    ) -> StreamKey:
        return super().__new__(
            cls,
            f"{namespace}-stream:{str(workspace_id)}:{str(session_id)}",
        )


# TypeAdapters for pydantic-ai message types - created lazily to avoid import overhead
# These are used by the legacy pydantic-ai harness, not the sandbox runtime
ClaudeSDKMessageTA: TypeAdapter[ClaudeSDKMessage] = TypeAdapter(ClaudeSDKMessage)


class _LazyTypeAdapter:
    """Lazy wrapper for TypeAdapter that imports pydantic-ai only when used."""

    def __init__(self, import_path: str, type_name: str):
        self._import_path = import_path
        self._type_name = type_name
        self._adapter: TypeAdapter[Any] | None = None

    def _ensure_adapter(self) -> TypeAdapter[Any]:
        if self._adapter is None:
            import importlib

            module = importlib.import_module(self._import_path)
            type_cls = getattr(module, self._type_name)
            self._adapter = TypeAdapter(type_cls)
        return self._adapter

    def validate_python(self, obj: Any) -> Any:
        return self._ensure_adapter().validate_python(obj)

    def dump_python(self, obj: Any, **kwargs: Any) -> Any:
        return self._ensure_adapter().dump_python(obj, **kwargs)

    def validate_json(self, data: bytes | str) -> Any:
        return self._ensure_adapter().validate_json(data)

    def dump_json(self, obj: Any, **kwargs: Any) -> bytes:
        return self._ensure_adapter().dump_json(obj, **kwargs)


# Lazy TypeAdapters that only import pydantic-ai when methods are called
ModelMessageTA: Any = _LazyTypeAdapter("pydantic_ai.messages", "ModelMessage")
ModelResponseTA: Any = _LazyTypeAdapter("pydantic_ai", "ModelResponse")

# Union type for messages from either harness
# At runtime, ModelMessage is Any so this is effectively Any | ClaudeSDKMessage
UnifiedMessage = ModelMessage | ClaudeSDKMessage


@runtime_checkable
class MessageStore(Protocol):
    async def load(self, session_id: uuid.UUID) -> list[ChatMessage]: ...

    async def store(
        self,
        session_id: uuid.UUID,
        messages: Sequence[UnifiedMessage],
        *,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None: ...


@runtime_checkable
class StreamingAgentDeps(Protocol):
    stream_writer: StreamWriter
    message_store: MessageStore | None = None


type OutputType = (
    Literal[
        "bool",
        "float",
        "int",
        "str",
        "list[bool]",
        "list[float]",
        "list[int]",
        "list[str]",
    ]
    | dict[str, Any]
)


@pydantic.dataclasses.dataclass(kw_only=True, slots=True)
class AgentConfig:
    """Configuration for an agent."""

    # Model
    model_name: str
    model_provider: str
    base_url: str | None = None
    # Agent
    instructions: str | None = None
    output_type: OutputType | None = None
    # Tools
    actions: list[str] | None = None
    namespaces: list[str] | None = None
    tool_approvals: dict[str, bool] | None = None
    # MCP
    model_settings: dict[str, Any] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    retries: int = TRACECAT__AGENT_MAX_RETRIES
    deps_type: type[Any] | None = None
    custom_tools: CustomToolList | None = None


# --- Deferred Tool Types (Harness-Agnostic) ---
# These types decouple Tracecat from pydantic-ai's internal types,
# enabling plug-and-play support for different agent harnesses (pydantic-ai, Claude SDK, etc.)


@dataclass(kw_only=True)
class ToolApproved:
    """Indicates that a tool call has been approved for execution."""

    override_args: dict[str, Any] | None = None
    """Optional arguments to use instead of the original arguments."""

    kind: Literal["tool-approved"] = "tool-approved"


@dataclass(kw_only=True)
class ToolDenied:
    """Indicates that a tool call has been denied."""

    message: str = "The tool call was denied."
    """Message to return to the model explaining the denial."""

    kind: Literal["tool-denied"] = "tool-denied"


DeferredToolApprovalResult = Annotated[ToolApproved | ToolDenied, Discriminator("kind")]
"""Result for a tool call that required human-in-the-loop approval."""


@dataclass(kw_only=True)
class DeferredToolRequests:
    """Harness-agnostic deferred tool requests.

    Represents tool calls that require approval or external execution
    before the agent can continue. Uses ToolCallContent for a harness-agnostic
    representation of tool calls.
    """

    approvals: list[ToolCallContent] = field(default_factory=list)
    """Tool calls that require human-in-the-loop approval."""

    calls: list[ToolCallContent] = field(default_factory=list)
    """Tool calls that require external execution."""

    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Metadata for deferred tool calls, keyed by tool_call_id."""


@dataclass(kw_only=True)
class DeferredToolResults:
    """Harness-agnostic deferred tool results.

    Results for deferred tool calls from a previous run. The tool call IDs
    must match those from the DeferredToolRequests output.
    """

    approvals: dict[str, bool | ToolApproved | ToolDenied] = field(default_factory=dict)
    """Map of tool call IDs to approval results (True = approved, or ToolApproved/ToolDenied)."""

    calls: dict[str, Any] = field(default_factory=dict)
    """Map of tool call IDs to results for externally executed tools."""
