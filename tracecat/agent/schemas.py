"""Public models for agentic execution."""

from __future__ import annotations as _annotations

import uuid
from typing import (
    Any,
    Literal,
    NotRequired,
    TypedDict,
)

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunUsage
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolResults

from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role


class ModelInfo(BaseModel):
    name: str
    provider: str
    base_url: str | None


class RunAgentArgs(BaseModel):
    user_prompt: str
    """User prompt for the agent."""
    session_id: uuid.UUID
    """Session ID for the agent execution."""
    config: AgentConfig | None = None
    """Configuration for the agent. Required if preset_slug is not provided."""
    preset_slug: str | None = None
    """Slug for the preset configuration (if using a preset)."""
    max_requests: int | None = None
    """Maximum number of requests for the agent."""
    max_tool_calls: int | None = None
    """Maximum number of tool calls for the agent."""
    deferred_tool_results: DeferredToolResults | None = None
    """Results for deferred tool calls from a previous run (CE handshake)."""
    is_continuation: bool = False
    """If True, do not emit a new user message; continue prior run with deferred results."""

    @model_validator(mode="after")
    def validate_config_or_preset(self) -> RunAgentArgs:
        """Ensure either config or preset_slug is provided."""
        if self.config is None and self.preset_slug is None:
            raise ValueError("Either 'config' or 'preset_slug' must be provided")
        return self


class ModelConfig(BaseModel):
    name: str = Field(
        ...,
        description="The name of the model. This is used to identify the model in the "
        "system.",
        min_length=1,
        max_length=100,
    )
    provider: str = Field(
        ...,
        description="The provider of the model. This is used to determine which "
        "organization secret to use for this model.",
        min_length=1,
        max_length=100,
    )
    org_secret_name: str = Field(
        ...,
        description="The name of the organization secret to use for this model. "
        "This secret must be configured in the organization settings.",
        min_length=1,
        max_length=200,
    )
    secrets: ModelSecretConfig = Field(
        ...,
        description="The secrets to use for this model. This is used to determine "
        "which organization secret to use for this model.",
    )


class ModelSecretConfig(TypedDict):
    required: NotRequired[list[str]]
    optional: NotRequired[list[str]]


class ProviderCredentialField(BaseModel):
    """Model for defining credential fields required by a provider."""

    key: str = Field(
        ...,
        description="The environment variable key for this credential",
        min_length=1,
        max_length=100,
    )
    label: str = Field(
        ...,
        description="Human-readable label for the field",
        min_length=1,
        max_length=200,
    )
    type: Literal["text", "password"] = Field(
        ..., description="Input type: 'text' or 'password'"
    )
    description: str = Field(
        ...,
        description="Help text describing this credential",
        min_length=1,
        max_length=500,
    )
    required: bool = Field(default=True, description="Whether this field is required")


class ProviderCredentialConfig(BaseModel):
    """Model for provider credential configuration."""

    provider: str = Field(
        ..., description="The provider name", min_length=1, max_length=100
    )
    label: str = Field(
        ...,
        description="Human-readable label for the provider",
        min_length=1,
        max_length=200,
    )
    fields: list[ProviderCredentialField] = Field(
        ..., description="Required credential fields"
    )


class ModelCredentialCreate(BaseModel):
    """Model for creating model credentials."""

    provider: str = Field(..., min_length=1, max_length=100)
    credentials: dict[str, str] = Field(
        ..., description="Provider-specific credentials (e.g., api_key)"
    )


class ModelCredentialUpdate(BaseModel):
    """Model for updating model credentials."""

    credentials: dict[str, str] = Field(
        ..., description="Provider-specific credentials to update"
    )


class AgentOutput(BaseModel):
    output: Any
    message_history: list[ModelMessage] | None = None
    duration: float
    usage: RunUsage
    session_id: uuid.UUID
    trace_id: str | None = None


class ExecuteToolCallArgs(BaseModel):
    tool_name: str = Field(..., description="Name of the tool to execute")
    tool_args: dict[str, Any] = Field(..., description="Arguments for the tool")
    tool_call_id: str = Field(..., description="ID of the tool call")


class ExecuteToolCallResult(BaseModel):
    type: Literal["result", "error", "retry"] = Field(..., description="Type of result")
    result: Any = Field(default=None, description="Tool return part")
    error: str | None = Field(
        default=None, description="Error message if execution failed"
    )
    retry_message: str | None = Field(
        default=None, description="Retry message if ModelRetry was raised"
    )


class ModelRequestArgs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    role: Role
    messages: list[ModelMessage]
    model_settings: ModelSettings | None
    model_request_parameters: ModelRequestParameters
    model_info: ModelInfo


class ModelRequestResult(BaseModel):
    model_response: ModelResponse = Field(..., description="Model response")


class ToolFilters(BaseModel):
    actions: list[str] | None = None
    namespaces: list[str] | None = None
