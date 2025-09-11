"""Public models for agentic execution."""

from __future__ import annotations as _annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from tracecat.types.auth import Role


class RunAgentArgs(BaseModel):
    role: Role
    user_prompt: str
    tool_filters: ToolFilters | None = None
    """This is static over the lifetime of the workflow, as it's for 1 turn."""
    session_id: str
    """Session ID for the agent execution."""
    instructions: str | None = None
    """Optional instructions for the agent. Defaults set in workflow."""
    model_info: ModelInfo
    """Model configuration."""


class RunAgentResult(BaseModel):
    messages: list[ModelMessage]


class ModelInfo(BaseModel):
    name: str
    provider: str
    base_url: str | None = None


class ToolFilters(BaseModel):
    actions: list[str] | None = None
    namespaces: list[str] | None = None

    @staticmethod
    def default() -> ToolFilters:
        return ToolFilters(
            actions=[
                "core.cases.create_case",
                "core.cases.get_case",
                "core.cases.list_cases",
                "core.cases.update_case",
                "core.cases.list_cases",
            ],
        )


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
