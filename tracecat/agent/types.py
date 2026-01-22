from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NotRequired,
    Protocol,
    TypedDict,
    runtime_checkable,
)

import pydantic
from claude_agent_sdk.types import Message as ClaudeSDKMessage
from pydantic import Discriminator, TypeAdapter

from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.config import TRACECAT__AGENT_MAX_RETRIES

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.tools import Tool as _PATool

    from tracecat.agent.stream.writers import StreamWriter

    CustomToolList = list[_PATool[Any]]
else:
    # Runtime fallbacks for types only used in annotations
    ModelMessage = Any
    CustomToolList = list[Any]


class MCPServerConfig(TypedDict):
    """Configuration for a user-defined MCP server.

    Users can connect custom MCP servers to their agents - whether running as
    Docker containers, local processes, or remote services. The server must
    expose an HTTP or SSE endpoint.

    Example:
        {
            "name": "internal-tools",
            "url": "http://host.docker.internal:8080",
            "transport": "http",
            "headers": {"Authorization": "Bearer ${{ SECRETS.internal.API_KEY }}"}
        }
    """

    name: str
    """Required: Unique identifier for the server. Tools will be prefixed with mcp__{name}__."""

    url: str
    """Required: HTTP/SSE endpoint URL for the MCP server."""

    headers: NotRequired[dict[str, str]]
    """Optional: Auth headers (can reference Tracecat secrets)."""

    transport: NotRequired[Literal["http", "sse"]]
    """Optional: Transport type. Defaults to 'http'."""


class StreamKey(str):
    def __new__(
        cls,
        workspace_id: uuid.UUID | str,
        session_id: uuid.UUID | str,
    ) -> StreamKey:
        return super().__new__(
            cls,
            f"agent-stream:{str(workspace_id)}:{str(session_id)}",
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
class StreamingAgentDeps(Protocol):
    stream_writer: StreamWriter


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
    # Sandbox
    enable_internet_access: bool = False


# --- Tool Types (Harness-Agnostic) ---
# These types decouple Tracecat from pydantic-ai's internal types,
# enabling plug-and-play support for different agent harnesses (pydantic-ai, Claude SDK, etc.)


@dataclass(kw_only=True, slots=True)
class Tool:
    """Harness-agnostic tool definition.

    Uses canonical action names (with dots) throughout. Harness-specific adapters
    are responsible for converting to their required format (e.g., pydantic-ai
    requires underscores for Python function names).

    Canonical names are used for:
    - JWT token authorization (mcp/executor.py checks canonical names)
    - Proxy server tool creation (expects canonical names, converts internally)
    - UX display and configuration
    """

    name: str
    """Canonical action name with dots (e.g., 'core.cases.list_cases')."""

    description: str
    """Human-readable description of what the tool does."""

    parameters_json_schema: dict[str, Any]
    """JSON schema for tool parameters."""

    requires_approval: bool = False
    """Whether this tool requires human approval before execution."""


# --- Deferred Tool Types (Harness-Agnostic) ---


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
