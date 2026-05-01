"""Lightweight types for agent sandbox communication."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.subagents import AgentsConfig

if TYPE_CHECKING:
    from tracecat.agent.types import AgentConfig


class MCPHttpServerConfig(TypedDict):
    """Configuration for a user-defined MCP server over HTTP/SSE.

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

    type: NotRequired[Literal["http"]]
    """Discriminator for HTTP-based MCP configs. Defaults to 'http' when omitted."""

    name: str
    """Required: Unique identifier for the server. Tools will be prefixed with mcp__{name}__."""

    url: str
    """Required: HTTP/SSE endpoint URL for the MCP server."""

    headers: NotRequired[dict[str, str]]
    """Optional: Auth headers (can reference Tracecat secrets)."""

    transport: NotRequired[Literal["http", "sse"]]
    """Optional: Transport type. Defaults to 'http'."""

    timeout: NotRequired[int]
    """Optional: Request timeout in seconds."""


class MCPStdioServerConfig(TypedDict):
    """Configuration for a stdio MCP server."""

    type: Literal["stdio"]
    name: str
    command: str
    args: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]
    timeout: NotRequired[int]


MCPServerConfig = MCPHttpServerConfig | MCPStdioServerConfig


@dataclass(kw_only=True, slots=True)
class MCPToolDefinition:
    """Tool definition for MCP proxy server.

    Contains all information needed to expose a tool to an agent runtime
    without database access.
    """

    name: str
    """Action name, e.g., 'core.http_request'."""

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


class SandboxAgentConfig(BaseModel):
    """Minimal agent configuration for sandbox execution.

    This is a lightweight version of AgentConfig that contains only
    the fields needed by the sandboxed runtime.
    """

    model_config = ConfigDict(extra="forbid")

    # Model
    model_name: str
    model_provider: str
    base_url: str | None = None
    passthrough: bool = False

    # Agent
    instructions: str | None = None

    # Tools
    tool_approvals: dict[str, bool] | None = None
    """Map of action names to whether they require approval."""

    # MCP
    mcp_servers: list[MCPServerConfig] | None = None
    """User-defined MCP servers to connect to."""

    # Subagents
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    """Canonical agents config for sandbox transport."""

    # Output
    output_type: str | dict[str, Any] | None = None
    """Expected output type for structured outputs (e.g., "int", "str", or a JSON schema dict)."""

    # Sandbox
    enable_thinking: bool = True
    """Whether to enable extended thinking for the Claude Code CLI."""
    enable_internet_access: bool = False
    """Whether to enable internet access tools (WebSearch, WebFetch)."""

    @classmethod
    def from_agent_config(cls, config: AgentConfig) -> SandboxAgentConfig:
        """Create from a full AgentConfig (Pydantic model).

        This extracts only the fields needed for sandbox execution.

        Args:
            config: AgentConfig instance (or any object with matching attributes).
        """
        return cls(
            model_name=config.model_name,
            model_provider=config.model_provider,
            base_url=config.base_url,
            passthrough=config.passthrough,
            instructions=config.instructions,
            tool_approvals=config.tool_approvals,
            mcp_servers=config.mcp_servers,
            agents=config.agents,
            output_type=config.output_type,
            enable_thinking=config.enable_thinking,
            enable_internet_access=config.enable_internet_access,
        )


class SandboxSubagentConfig(BaseModel):
    """Fully resolved subagent configuration for sandbox execution.

    The trusted workflow resolves preset references, discovers tools, and mints
    scope-specific tokens before this reaches the sandbox runtime.
    """

    model_config = ConfigDict(extra="forbid")

    alias: str
    description: str
    prompt: str
    config: SandboxAgentConfig
    mcp_auth_token: str
    max_turns: int | None = None
    allowed_actions: dict[str, MCPToolDefinition] | None = None


def sandbox_requires_internet_access(
    config: SandboxAgentConfig,
    subagents: Iterable[SandboxSubagentConfig],
) -> bool:
    """Return whether the sandbox process needs network access."""
    return config.enable_internet_access or any(
        subagent.config.enable_internet_access for subagent in subagents
    )
