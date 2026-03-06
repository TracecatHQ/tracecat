"""Workflow-safe schemas for agent configuration.

These models avoid importing agent runtime dependencies so they can be used
across Temporal workflow/activity boundaries predictably.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field


class MCPHttpServerConfigPayload(BaseModel):
    """Workflow-safe HTTP/SSE MCP server config."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = Field(default="http")
    name: str
    url: str
    headers: dict[str, str] | None = Field(default=None)
    transport: Literal["http", "sse"] | None = Field(default=None)
    timeout: int | None = Field(default=None)


class MCPStdioServerConfigPayload(BaseModel):
    """Workflow-safe stdio MCP server config."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"] = Field(default="stdio")
    name: str
    command: str
    args: list[str] | None = Field(default=None)
    env: dict[str, str] | None = Field(default=None)
    timeout: int | None = Field(default=None)


type MCPServerConfigPayload = Annotated[
    MCPHttpServerConfigPayload | MCPStdioServerConfigPayload,
    Discriminator("type"),
]


class AgentConfigPayload(BaseModel):
    """Workflow-safe agent config payload.

    This intentionally contains only JSON-safe fields needed across Temporal
    boundaries. Runtime-only fields like deps_type and custom_tools are omitted.
    """

    model_config = ConfigDict(extra="forbid")

    model_name: str
    model_provider: str
    base_url: str | None = Field(default=None)
    instructions: str | None = Field(default=None)
    output_type: str | dict[str, Any] | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    model_settings: dict[str, Any] | None = Field(default=None)
    mcp_servers: list[MCPServerConfigPayload] | None = Field(default=None)
    retries: int
    enable_internet_access: bool = Field(default=False)
