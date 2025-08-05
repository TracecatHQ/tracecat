from __future__ import annotations as _annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings


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


class AgenticTurnArgs(BaseModel):
    chat_id: str = Field(..., description="Chat session identifier")
    message_id: str = Field(..., description="Unique message identifier")
    user_text: str = Field(..., description="User message text")
    tool_filters: ToolFilters | None = Field(
        default=None, description="Tool filters for this turn"
    )


class AgenticTurnResult(BaseModel):
    final_response: str = Field(..., description="Final agent response")
    tool_calls_made: int = Field(..., description="Number of tool calls executed")
    turns_used: int = Field(..., description="Number of agentic turns used")


class ReadConversationContextArgs(BaseModel):
    chat_id: str = Field(..., description="Chat session identifier")
    limit: int = Field(default=50, description="Maximum number of messages to retrieve")


class ReadConversationContextResult(BaseModel):
    messages: bytes = Field(..., description="Serialized message history")


class PersistConversationTurnArgs(BaseModel):
    chat_id: str = Field(..., description="Chat session identifier")
    message_id: str = Field(..., description="Message identifier")
    messages: bytes = Field(..., description="Serialized message history to persist")


class PersistConversationTurnResult(BaseModel):
    success: bool = Field(..., description="Whether persist operation succeeded")


class AgentTurnWorkflowArgs(BaseModel):
    user_prompt: str
    tool_filters: ToolFilters | None = None


@dataclass
class AgentDeps:
    call_model: Callable[[ModelRequestArgs], Awaitable[ModelRequestResult]]
    call_tool: Callable[[ExecuteToolCallArgs], Awaitable[ExecuteToolCallResult]]


class ModelInfo(BaseModel):
    name: str
    provider: str
    base_url: str | None = None


class DurableModelRequestArgs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    messages: list[ModelMessage]
    model_settings: ModelSettings | None
    model_request_parameters: ModelRequestParameters
    model_info: ModelInfo
    tool_filters: ToolFilters | None = None


ModelResponseTA: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)
