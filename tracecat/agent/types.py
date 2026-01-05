from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
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
from pydantic_ai import ModelResponse
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import Tool as _PATool

from tracecat import config
from tracecat.agent.stream.types import ToolCallContent
from tracecat.chat.enums import MessageKind

if TYPE_CHECKING:
    from pydantic_ai.tools import Tool as _PATool

    from tracecat.agent.sandbox.protocol import RuntimeInitPayload
    from tracecat.agent.sandbox.socket_io import SocketStreamWriter
    from tracecat.agent.stream.writers import StreamWriter
    from tracecat.chat.schemas import ChatMessage

    CustomToolList = list[_PATool[Any]]
else:  # pragma: no cover - runtime type hint fallback to appease pydantic
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


ModelMessageTA: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
ModelResponseTA: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)
ClaudeSDKMessageTA: TypeAdapter[ClaudeSDKMessage] = TypeAdapter(ClaudeSDKMessage)

# Union type for messages from either harness
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
    retries: int = config.TRACECAT__AGENT_MAX_RETRIES
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


class AgentRuntime(ABC):
    """Abstract base class for sandboxed agent runtimes.

    Agent runtimes execute inside an NSJail sandbox without database access.
    All I/O happens via Unix sockets:
    - Control socket: Receives RuntimeInitPayload, streams events back
    - MCP socket: Tool execution via trusted MCP server

    The orchestrator (outside the sandbox) handles:
    - Session persistence
    - Message persistence
    - Approval flow coordination

    Implementations:
    - ClaudeAgentRuntime: Uses Claude Agent SDK
    """

    _socket_writer: SocketStreamWriter

    @property
    def socket_writer(self) -> SocketStreamWriter:
        """Socket writer for streaming events to orchestrator."""
        return self._socket_writer

    @abstractmethod
    async def run(self, payload: RuntimeInitPayload) -> None:
        """Run an agent with the given initialization payload.

        This is the main entry point for sandboxed execution.
        Called after receiving init payload from orchestrator via socket.

        The runtime should:
        1. Parse the payload for config, tools, and session data
        2. Set up the agent with MCP proxy for tool calls
        3. Execute the agent loop
        4. Stream events back to orchestrator via socket_writer
        5. Handle approval requests by interrupting and streaming APPROVAL_REQUEST
        6. Call socket_writer.send_done() when complete

        Args:
            payload: Initialization payload from orchestrator containing
                session info, config, tools, and optional resume data.

        Note:
            This method should not return a value. All output is streamed
            via socket_writer.
        """
        ...
