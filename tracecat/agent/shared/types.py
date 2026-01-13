"""Lightweight types for agent sandbox communication.

Pure dataclasses with no Pydantic dependencies for minimal import footprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict


class MCPServerConfig(TypedDict):
    """Configuration for an MCP server."""

    url: str
    headers: dict[str, str]


@dataclass(kw_only=True, slots=True)
class MCPToolDefinition:
    """Tool definition for MCP proxy server.

    Contains all information needed to expose a tool to Claude
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
        )

    @classmethod
    def from_agent_config(cls, config: Any) -> SandboxAgentConfig:
        """Create from a full AgentConfig (Pydantic model).

        This extracts only the fields needed for sandbox execution.

        Args:
            config: AgentConfig instance (or any object with matching attributes).
        """
        return cls(
            model_name=config.model_name,
            model_provider=config.model_provider,
            base_url=config.base_url,
            instructions=config.instructions,
            tool_approvals=config.tool_approvals,
            mcp_servers=config.mcp_servers,
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
        return result
