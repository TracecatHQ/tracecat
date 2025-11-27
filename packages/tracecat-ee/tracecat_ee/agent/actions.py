from typing import Any

from pydantic import BaseModel

from tracecat.agent.types import OutputType


class AgentActionArgs(BaseModel):
    user_prompt: str
    model_name: str
    model_provider: str
    actions: list[str]
    instructions: str | None = None
    output_type: OutputType | None = None
    model_settings: dict[str, Any] | None = None
    max_tool_calls: int = 15
    max_requests: int = 45
    retries: int = 3
    base_url: str | None = None
    tool_approvals: dict[str, bool] | None = None


class PresetAgentActionArgs(BaseModel):
    preset: str
    user_prompt: str
    actions: list[str] | None = None
    instructions: str | None = None
    max_tool_calls: int = 15
    max_requests: int = 45
