from typing import Any

from pydantic import BaseModel, Field

from tracecat import config
from tracecat.agent.types import OutputType


class AgentActionArgs(BaseModel):
    user_prompt: str
    model_name: str
    model_provider: str
    actions: list[str] | None = None
    instructions: str | None = None
    output_type: OutputType | None = None
    model_settings: dict[str, Any] | None = None
    max_tool_calls: int = Field(
        default=15,
        ge=1,
        le=config.TRACECAT__AGENT_MAX_TOOL_CALLS,
        description="The maximum number of tool calls to make per agent run",
    )
    max_requests: int = Field(
        default=45,
        ge=1,
        le=config.TRACECAT__AGENT_MAX_REQUESTS,
        description="The maximum number of model requests to make per agent run",
    )
    retries: int = 3
    base_url: str | None = None
    tool_approvals: dict[str, bool] | None = None
    use_workspace_credentials: bool = Field(
        default=True,
        description="If True, use workspace-scoped credentials; otherwise org-level",
    )


class PresetAgentActionArgs(BaseModel):
    preset: str
    user_prompt: str
    actions: list[str] | None = None
    instructions: str | None = None
    max_tool_calls: int = Field(
        default=15,
        ge=1,
        le=config.TRACECAT__AGENT_MAX_TOOL_CALLS,
        description="The maximum number of tool calls to make per agent run",
    )
    max_requests: int = Field(
        default=45,
        ge=1,
        le=config.TRACECAT__AGENT_MAX_REQUESTS,
        description="The maximum number of model requests to make per agent run",
    )
    use_workspace_credentials: bool = Field(
        default=True,
        description="If True, use workspace-scoped credentials; otherwise org-level",
    )
