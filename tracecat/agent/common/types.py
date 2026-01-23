"""Lightweight types for agent sandbox communication.

Pure dataclasses with no Pydantic dependencies for minimal import footprint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from pydantic_ai.tools import Tool as _PATool

    CustomToolList = list[_PATool[Any]]
else:
    CustomToolList = list[Any]


# --- MCP Configuration Types ---


class MCPServerConfig(TypedDict):
    """Configuration for a URL-based MCP server (HTTP/SSE).

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


class MCPCommandServerConfig(TypedDict):
    """Configuration for a command-based MCP server (stdio).

    These servers run as subprocesses and communicate via stdio. They are spawned
    fresh for each agent invocation inside the sandbox.

    Example:
        {
            "name": "github",
            "command": "npx",
            "args": ["@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_xxx"}
        }
    """

    name: str
    """Required: Unique identifier for the server. Tools will be prefixed with mcp__{name}__."""

    command: str
    """Required: Command to run (must be in allowlist: npx, uvx, python, python3, node)."""

    args: NotRequired[list[str]]
    """Optional: Arguments for the command."""

    env: NotRequired[dict[str, str]]
    """Optional: Environment variables for the subprocess (secrets already resolved)."""

    timeout: NotRequired[int]
    """Optional: Process timeout in seconds. Defaults to 30."""


# --- MCP Tool Definition ---


@dataclass(kw_only=True, slots=True)
class MCPToolDefinition:
    """Tool definition for MCP proxy server.

    Contains all information needed to expose a tool to an agent runtime
    without database access.
    """

    name: str
    """Action name, e.g., 'tools.slack.post_message'."""

    description: str
    """Human-readable description of what the tool does."""

    parameters_json_schema: dict[str, Any]
    """JSON Schema for the tool's input parameters."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPToolDefinition:
        """Construct from dict (orjson parsed)."""
        return cls(
            name=data["name"],
            description=data["description"],
            parameters_json_schema=data["parameters_json_schema"],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters_json_schema": self.parameters_json_schema,
        }


# --- Stream Key ---


class StreamKey(str):
    """Redis stream key for agent sessions."""

    def __new__(
        cls,
        workspace_id: uuid.UUID | str,
        session_id: uuid.UUID | str,
    ) -> StreamKey:
        return super().__new__(
            cls,
            f"agent-stream:{str(workspace_id)}:{str(session_id)}",
        )


# --- Output Type ---


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


# --- Agent Configuration ---


@dataclass(kw_only=True, slots=True)
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
    # MCP - URL-based servers (HTTP/SSE)
    model_settings: dict[str, Any] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    # MCP - Command-based servers (stdio)
    mcp_command_servers: list[MCPCommandServerConfig] | None = None
    retries: int = 3
    deps_type: type[Any] | None = None
    custom_tools: CustomToolList | None = None
    # Sandbox
    enable_internet_access: bool = False


@dataclass(kw_only=True, slots=True)
class SandboxAgentConfig:
    """Minimal agent configuration for sandbox execution.

    This is a lightweight version of AgentConfig that contains only
    the fields needed by the sandboxed runtime.
    """

    # Model
    model_name: str
    model_provider: str
    base_url: str | None = None

    # Agent
    instructions: str | None = None

    # Tools
    tool_approvals: dict[str, bool] | None = None
    """Map of action names to whether they require approval."""

    # MCP
    mcp_servers: list[MCPServerConfig] | None = None
    """User-defined MCP servers to connect to."""

    # Output
    output_type: str | dict[str, Any] | None = None
    """Expected output type for structured outputs (e.g., "int", "str", or a JSON schema dict)."""

    # Sandbox
    enable_internet_access: bool = False
    """Whether to enable internet access tools (WebSearch, WebFetch)."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxAgentConfig:
        """Construct from dict (orjson parsed)."""
        return cls(
            model_name=data["model_name"],
            model_provider=data["model_provider"],
            base_url=data.get("base_url"),
            instructions=data.get("instructions"),
            tool_approvals=data.get("tool_approvals"),
            mcp_servers=data.get("mcp_servers"),
            output_type=data.get("output_type"),
            enable_internet_access=data.get("enable_internet_access", False),
        )

    @classmethod
    def from_agent_config(cls, config: AgentConfig) -> SandboxAgentConfig:
        """Create from a full AgentConfig.

        This extracts only the fields needed for sandbox execution.
        """
        return cls(
            model_name=config.model_name,
            model_provider=config.model_provider,
            base_url=config.base_url,
            instructions=config.instructions,
            tool_approvals=config.tool_approvals,
            mcp_servers=config.mcp_servers,
            output_type=config.output_type,
            enable_internet_access=config.enable_internet_access,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        result: dict[str, Any] = {
            "model_name": self.model_name,
            "model_provider": self.model_provider,
        }
        if self.base_url is not None:
            result["base_url"] = self.base_url
        if self.instructions is not None:
            result["instructions"] = self.instructions
        if self.tool_approvals is not None:
            result["tool_approvals"] = self.tool_approvals
        if self.mcp_servers is not None:
            result["mcp_servers"] = self.mcp_servers
        if self.output_type is not None:
            result["output_type"] = self.output_type
        result["enable_internet_access"] = self.enable_internet_access
        return result


# --- Tool Types (Harness-Agnostic) ---


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


def _get_deferred_tool_approval_discriminator(v: Any) -> str:
    """Discriminator function for DeferredToolApprovalResult."""
    if isinstance(v, dict):
        return v.get("kind", "tool-approved")
    return getattr(v, "kind", "tool-approved")


DeferredToolApprovalResult = Annotated[
    ToolApproved | ToolDenied, _get_deferred_tool_approval_discriminator
]
"""Result for a tool call that required human-in-the-loop approval."""


@dataclass(kw_only=True)
class DeferredToolRequests:
    """Harness-agnostic deferred tool requests.

    Represents tool calls that require approval or external execution
    before the agent can continue. Uses ToolCallContent for a harness-agnostic
    representation of tool calls.
    """

    approvals: list[Any] = field(default_factory=list)
    """Tool calls that require human-in-the-loop approval (ToolCallContent instances)."""

    calls: list[Any] = field(default_factory=list)
    """Tool calls that require external execution (ToolCallContent instances)."""

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
