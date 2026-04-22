import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracecat import config
from tracecat.agent.types import OutputType

# ``extra="ignore"`` keeps Temporal activity replay working after the legacy
# ``use_workspace_credentials`` field was dropped — in-flight workflow history
# still carries the old key and pydantic will silently drop it rather than
# error.
_BASE_CONFIG = ConfigDict(extra="ignore")


class AgentActionArgs(BaseModel):
    model_config = _BASE_CONFIG

    user_prompt: str
    model_name: str
    model_provider: str
    actions: list[str] | None = None
    instructions: str | None = None
    output_type: OutputType | None = None
    session_id: uuid.UUID | None = None
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
    enable_thinking: bool = True
    base_url: str | None = None
    tool_approvals: dict[str, bool] | None = None


class PresetAgentActionArgs(BaseModel):
    model_config = _BASE_CONFIG

    preset: str
    preset_version: int | None = None
    user_prompt: str
    actions: list[str] | None = None
    instructions: str | None = None
    session_id: uuid.UUID | None = None
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
