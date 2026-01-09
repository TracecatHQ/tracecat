"""Type definitions for MCP servers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MCPToolDefinition(BaseModel):
    """Tool definition for MCP proxy server.

    Follows Pydantic AI's ToolDefinition pattern. Contains all information
    needed to expose a tool to Claude without database access.
    """

    name: str
    """Action name, e.g., 'tools.slack.post_message'."""

    description: str
    """Human-readable description of what the tool does."""

    parameters_json_schema: dict[str, Any]
    """JSON Schema for the tool's input parameters."""
