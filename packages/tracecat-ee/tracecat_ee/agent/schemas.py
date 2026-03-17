from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from tracecat_registry.sdk.agents import parse_model_selection

from tracecat import config
from tracecat.agent.types import OutputType


class AgentActionArgs(BaseModel):
    user_prompt: str
    model_name: str
    model_provider: str
    source_id: UUID | None = None
    model: str | None = Field(default=None)
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

    @model_validator(mode="before")
    @classmethod
    def normalize_model_selection(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not isinstance(model := value.get("model"), str):
            return value

        normalized = dict(value)
        selection = parse_model_selection(model)
        normalized_source_id = (
            UUID(selection["source_id"]) if selection["source_id"] is not None else None
        )
        explicit_source_id = normalized.get("source_id")
        if isinstance(explicit_source_id, str):
            explicit_source_id = UUID(explicit_source_id)
        if (
            explicit_source_id is not None
            and explicit_source_id != normalized_source_id
        ):
            raise ValueError("model conflicts with source_id")
        if (
            normalized.get("model_provider") is not None
            and normalized["model_provider"] != selection["model_provider"]
        ):
            raise ValueError("model conflicts with model_provider")
        if (
            normalized.get("model_name") is not None
            and normalized["model_name"] != selection["model_name"]
        ):
            raise ValueError("model conflicts with model_name")

        if normalized.get("source_id") is None:
            normalized["source_id"] = normalized_source_id
        if normalized.get("model_provider") is None:
            normalized["model_provider"] = selection["model_provider"]
        if normalized.get("model_name") is None:
            normalized["model_name"] = selection["model_name"]
        return normalized


class PresetAgentActionArgs(BaseModel):
    preset: str
    preset_version: int | None = None
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
