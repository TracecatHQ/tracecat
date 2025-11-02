"""Public models for agentic execution."""

from __future__ import annotations as _annotations

import uuid
from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field
from pydantic_ai import RunUsage
from pydantic_ai.messages import ModelMessage

from tracecat.agent.types import AgentConfig, OutputType


class RunAgentArgs(BaseModel):
    user_prompt: str
    """User prompt for the agent."""
    session_id: uuid.UUID
    """Session ID for the agent execution."""
    config: AgentConfig
    """Configuration for the agent."""
    max_requests: int | None = None
    """Maximum number of requests for the agent."""
    max_tool_calls: int | None = None
    """Maximum number of tool calls for the agent."""


class RunAgentResult(BaseModel):
    messages: list[ModelMessage]


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
    message_history: list[ModelMessage]
    duration: float
    usage: RunUsage
    session_id: uuid.UUID
    trace_id: str | None = None
