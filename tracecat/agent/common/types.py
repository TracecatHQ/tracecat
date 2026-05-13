"""Lightweight types for agent sandbox communication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, TypeGuard

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.subagents import AgentSubagentsConfig

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
    """Optional: Auth headers (can reference Tracecat secrets).

    Only populated when the config has been resolved at the trusted edge for
    immediate use. Boundary-crossing configs omit this field; the trusted
    caller re-resolves secrets per use via the source ``id``.
    """

    transport: NotRequired[Literal["http", "sse"]]
    """Optional: Transport type. Defaults to 'http'."""

    timeout: NotRequired[int]
    """Optional: Request timeout in seconds."""

    id: NotRequired[str]
    """Optional: UUID of the source ``mcp_integrations`` row this config was
    resolved from. Set when produced by ``AgentPresetService`` resolvers so
    callers can re-resolve secrets per use without re-listing integrations."""


class MCPStdioServerConfig(TypedDict):
    """Configuration for a stdio MCP server."""

    type: Literal["stdio"]
    name: str
    command: str
    args: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]
    """Optional: Process env vars. Treated as secrets; omitted at
    boundary-crossing producers — re-resolved at the trusted edge."""
    timeout: NotRequired[int]
    id: NotRequired[str]
    """Optional: UUID of the source ``mcp_integrations`` row this config was
    resolved from. See :class:`MCPHttpServerConfig.id`."""


MCPServerConfig = MCPHttpServerConfig | MCPStdioServerConfig


def is_stdio_mcp_server(config: MCPServerConfig) -> TypeGuard[MCPStdioServerConfig]:
    """Narrow a generic ``MCPServerConfig`` to its stdio variant."""
    return config.get("type") == "stdio"


def is_http_mcp_server(config: MCPServerConfig) -> TypeGuard[MCPHttpServerConfig]:
    """Narrow a generic ``MCPServerConfig`` to its HTTP variant.

    HTTP is the default discriminator (the ``type`` key is optional on
    ``MCPHttpServerConfig`` and defaults to ``"http"``), so configs without
    an explicit ``type`` are treated as HTTP for backwards compatibility.
    """
    return config.get("type", "http") == "http"


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
    # Preserve the selected custom-provider catalog row through sandbox startup so
    # direct passthrough credentials can be resolved per agent, including subagents.
    catalog_id: uuid.UUID | None = None
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
    agents: AgentSubagentsConfig = Field(default_factory=AgentSubagentsConfig)
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
            catalog_id=config.catalog_id,
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
    model_route: str | None = None
    max_turns: int | None = None
    allowed_actions: dict[str, MCPToolDefinition] | None = None
