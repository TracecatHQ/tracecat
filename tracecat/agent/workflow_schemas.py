"""Workflow-safe schemas for agent configuration.

These models avoid importing agent runtime dependencies so they can be used
across Temporal workflow/activity boundaries predictably.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, model_validator

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.auth.types import Role
from tracecat.integrations.schemas import MCPToolStatus

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
    id: str | None = Field(default=None)
    """UUID of the source ``mcp_integrations`` row. Lets trusted callers
    re-resolve secrets per use without carrying them through workflow
    history."""


class MCPServerToolSummaryPayload(BaseModel):
    """Workflow-safe, non-secret summary of a verified user MCP tool."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    requires_approval: bool = Field(default=False)
    status: MCPToolStatus = Field(default="available")


class MCPStdioServerConfigPayload(BaseModel):
    """Workflow-safe stdio MCP server config."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"] = Field(default="stdio")
    name: str
    command: str
    args: list[str] | None = Field(default=None)
    env: dict[str, str] | None = Field(default=None)
    timeout: int | None = Field(default=None)
    id: str | None = Field(default=None)
    """UUID of the source ``mcp_integrations`` row. See
    :class:`MCPHttpServerConfigPayload.id`."""
    tools: list[MCPServerToolSummaryPayload] | None = Field(default=None)
    """Latest verified stdio tool summaries. Non-secret and safe for workflow
    history."""


type MCPServerConfigPayload = Annotated[
    MCPHttpServerConfigPayload | MCPStdioServerConfigPayload,
    Discriminator("type"),
]


class ResolvedSkillRefPayload(BaseModel):
    """Workflow-safe resolved skill version reference."""

    model_config = ConfigDict(extra="forbid")

    skill_id: uuid.UUID
    skill_name: str
    skill_version_id: uuid.UUID
    manifest_sha256: str


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
    catalog_id: uuid.UUID | None = Field(default=None)
    base_url: str | None = Field(default=None)
    passthrough: bool = Field(default=False)
    instructions: str | None = Field(default=None)
    output_type: str | dict[str, Any] | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    model_settings: dict[str, Any] | None = Field(default=None)
    mcp_servers: list[MCPServerConfigPayload] | None = Field(default=None)
    agents: AgentSubagentsConfig = Field(default_factory=AgentSubagentsConfig)
    retries: int
    enable_thinking: bool = Field(default=True)
    enable_internet_access: bool = Field(default=False)
    resolved_skills: list[ResolvedSkillRefPayload] | None = Field(default=None)
    builtin_skills: list[str] | None = Field(default=None)


class InlineAgentSource(BaseModel):
    """Inline configuration to resolve for one durable agent turn."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: Literal["inline"] = Field(default="inline")
    config: AgentConfigPayload


class PresetAgentSource(BaseModel):
    """Preset reference and supported overrides for one durable agent turn."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: Literal["preset"] = Field(default="preset")
    slug: str
    version: int | None = Field(default=None)
    preset_id: uuid.UUID | None = Field(default=None)
    preset_version_id: uuid.UUID | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    instructions: str | None = Field(default=None)


type AgentSource = Annotated[
    InlineAgentSource | PresetAgentSource,
    Discriminator("kind"),
]


class AgentTurnRequest(BaseModel):
    """Unresolved, immutable request for one durable agent turn."""

    # This payload is stored in Temporal history. Ignore stale keys so future
    # contract cleanup does not make earlier executions undecodable.
    model_config = ConfigDict(extra="ignore", frozen=True)

    role: Role
    session_id: uuid.UUID
    active_stream_id: uuid.UUID | None = Field(default=None)
    user_prompt: str
    source: AgentSource

    title: str = Field(default="New Chat")
    entity_type: AgentSessionEntity
    entity_id: uuid.UUID
    tools: list[str] | None = Field(default=None)
    harness_type: HarnessType = Field(default=HarnessType.CLAUDE_CODE)
    continue_existing_session: bool = Field(default=False)

    max_requests: int | None = Field(default=None)
    max_tool_calls: int | None = Field(default=None)
