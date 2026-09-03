from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import pydantic
from claude_agent_sdk.types import Message as ClaudeSDKMessage
from pydantic import Discriminator, TypeAdapter

from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.constants import AGENT_TIMEOUT_SECONDS_DEFAULT
from tracecat.agent.skill.types import ResolvedSkillRef
from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.config import (
    TRACECAT__AGENT_MAX_RETRIES,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
)

CustomToolList = list[Any]


def clamp_agent_timeout_seconds(timeout_seconds: int | None) -> int:
    """Clamp an agent timeout to the deployment ceiling.

    ``None`` inherits the hardcoded default; explicit values clamp to
    [default, ceiling]. Never rejects: out-of-bounds values clamp.
    """
    ceiling = TRACECAT__AGENT_SANDBOX_TIMEOUT
    floor = min(AGENT_TIMEOUT_SECONDS_DEFAULT, ceiling)
    if timeout_seconds is None:
        return floor
    return min(max(timeout_seconds, floor), ceiling)


class StreamKey(str):
    def __new__(
        cls,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        stream_id: uuid.UUID | None = None,
    ) -> StreamKey:
        base = f"agent-stream:{workspace_id}:{session_id}"
        return super().__new__(
            cls,
            f"{base}:{stream_id}" if stream_id else base,
        )


ClaudeSDKMessageTA: TypeAdapter[ClaudeSDKMessage] = TypeAdapter(ClaudeSDKMessage)

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
    catalog_id: uuid.UUID | None = None
    """Catalog row backing this model selection. When set, credentials and
    (for cloud/custom providers) the invocation target resolve from
    ``agent_catalog.encrypted_config`` instead of the legacy
    ``agent-{provider}-credentials`` secret."""
    base_url: str | None = None
    passthrough: bool = False
    # Agent
    instructions: str | None = None
    output_type: str | dict[str, Any] | None = None
    # Tools
    actions: list[str] | None = None
    namespaces: list[str] | None = None
    tool_approvals: dict[str, bool] | None = None
    # MCP
    model_settings: dict[str, Any] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    # Subagents
    agents: AgentSubagentsConfig = field(default_factory=AgentSubagentsConfig)
    retries: int = TRACECAT__AGENT_MAX_RETRIES
    deps_type: type[Any] | None = None
    custom_tools: CustomToolList | None = None
    # Sandbox
    enable_thinking: bool = True
    enable_internet_access: bool = False
    max_output_tokens: int | None = None
    """Per-request output token cap from the model catalog entry."""
    resolved_skills: list[ResolvedSkillRef] | None = None
    builtin_skills: list[str] | None = None
    """Names of built-in platform skills to stage into the agent's skills
    directory, independent of preset-bound ``resolved_skills``. Names only (not
    host paths) so the value is Temporal-replay-safe; the executor resolves each
    name to a packaged skill directory at stage time.

    Unlike ``resolved_skills``, these carry no version or manifest digest:
    built-in skills ship inside the ``tracecat_ee`` package, so their content
    is pinned by the deployed code version itself. A name here always stages
    whatever the executor's installed package contains — there is no separate
    artifact to pin or verify."""


# --- Tool Types (Harness-Agnostic) ---


@dataclass(kw_only=True, slots=True)
class Tool:
    """Harness-agnostic tool definition.

    Uses canonical action names with dots throughout.

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
