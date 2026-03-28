from typing import Any

from pydantic import BaseModel, Field

from tracecat.agent.schemas import (
    DEFAULT_PRESET_AGENT_MAX_REQUESTS,
    DEFAULT_PRESET_AGENT_MAX_TOOL_CALLS,
    AgentMaxRequests,
    AgentMaxToolCalls,
)
from tracecat.agent.types import OutputType


class AgentActionArgs(BaseModel):
    user_prompt: str
    model_name: str
    model_provider: str
    actions: list[str] | None = None
    instructions: str | None = None
    output_type: OutputType | None = None
    model_settings: dict[str, Any] | None = None
    max_tool_calls: AgentMaxToolCalls = DEFAULT_PRESET_AGENT_MAX_TOOL_CALLS
    max_requests: AgentMaxRequests = DEFAULT_PRESET_AGENT_MAX_REQUESTS
    retries: int = 3
    base_url: str | None = None
    tool_approvals: dict[str, bool] | None = None
    use_workspace_credentials: bool = Field(
        default=True,
        description="If True, use workspace-scoped credentials; otherwise org-level",
    )


class PresetAgentActionArgs(BaseModel):
    preset: str
    preset_version: int | None = None
    user_prompt: str
    actions: list[str] | None = None
    instructions: str | None = None
    max_tool_calls: AgentMaxToolCalls = DEFAULT_PRESET_AGENT_MAX_TOOL_CALLS
    max_requests: AgentMaxRequests = DEFAULT_PRESET_AGENT_MAX_REQUESTS
    use_workspace_credentials: bool = Field(
        default=True,
        description="If True, use workspace-scoped credentials; otherwise org-level",
    )
