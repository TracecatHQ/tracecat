from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field


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


class ToolFilters(BaseModel):
    actions: list[str] | None = None
    namespaces: list[str] | None = None


class ModelRequestArgs(BaseModel):
    message_history: bytes = Field(..., description="Serialized message history")
    tool_filters: ToolFilters = Field(
        default_factory=ToolFilters, description="Tool filters"
    )


class ModelRequestResult(BaseModel):
    model_response: bytes = Field(..., description="Serialized model response")


class ExecuteToolCallArgs(BaseModel):
    tool_name: str = Field(..., description="Name of the tool to execute")
    tool_args: dict[str, Any] = Field(..., description="Arguments for the tool")
    tool_call_id: str = Field(..., description="ID of the tool call")


class ExecuteToolCallResult(BaseModel):
    tool_return: bytes = Field(..., description="Serialized tool return part")
    error: str | None = Field(
        default=None, description="Error message if execution failed"
    )
