"""Workflow-safe schemas for agent configuration.

These models avoid importing agent runtime dependencies so they can be used
across Temporal workflow/activity boundaries predictably.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, model_validator

_LEGACY_AGENT_CONFIG_KEYS = frozenset({"deps_type", "custom_tools"})


def _normalize_legacy_mcp_server_payload(value: Any) -> Any:
    """Backfill the HTTP discriminator for MCP configs recorded before payload split."""
    if not isinstance(value, dict):
        return value

    match value:
        case {"type": str()}:
            return value
        case {"url": str()}:
            return {"type": "http", **value}
        case _:
            return value


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

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_workflow_history(cls, value: Any) -> Any:
        """Accept the pre-payload AgentConfig shape stored in workflow history."""
        if not isinstance(value, dict):
            return value

        normalized = {
            key: item
            for key, item in value.items()
            if key not in _LEGACY_AGENT_CONFIG_KEYS
        }
        if isinstance(mcp_servers := normalized.get("mcp_servers"), list):
            normalized["mcp_servers"] = [
                _normalize_legacy_mcp_server_payload(server) for server in mcp_servers
            ]
        return normalized

    model_name: str
    model_provider: str
    base_url: str | None = Field(default=None)
    passthrough: bool = Field(default=False)
    instructions: str | None = Field(default=None)
    output_type: str | dict[str, Any] | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    model_settings: dict[str, Any] | None = Field(default=None)
    mcp_servers: list[MCPServerConfigPayload] | None = Field(default=None)
    retries: int
    enable_thinking: bool = Field(default=True)
    enable_internet_access: bool = Field(default=False)
