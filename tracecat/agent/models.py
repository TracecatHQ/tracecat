"""Public models for agentic execution."""

from __future__ import annotations as _annotations

import uuid
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai import RunUsage
from pydantic_ai.messages import ModelMessage

from tracecat.agent.stream.writers import StreamWriter

ModelMessageTA: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


class MessageStore(Protocol):
    async def load(self, session_id: uuid.UUID) -> list[ModelMessage]: ...
    async def store(
        self, session_id: uuid.UUID, messages: list[ModelMessage]
    ) -> None: ...


class StreamingAgentDeps(Protocol):
    stream_writer: StreamWriter
    message_store: MessageStore | None = None


class RunAgentArgs(BaseModel):
    user_prompt: str
    tool_filters: ToolFilters | None = None
    """This is static over the lifetime of the workflow, as it's for 1 turn."""
    session_id: uuid.UUID
    """Session ID for the agent execution."""
    instructions: str | None = None
    """Optional instructions for the agent. Defaults set in workflow."""
    model_info: ModelInfo
    """Model configuration."""
    max_steps: int | None = None
    """Maximum number of steps for the agent."""
    max_tool_calls: int | None = None
    """Maximum number of tool calls for the agent."""
    output_type: OutputType | None = None
    """Desired output type for the agent's response."""


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


class AgentOutput(BaseModel):
    output: Any
    message_history: list[ModelMessage]
    duration: float
    usage: RunUsage
    session_id: uuid.UUID
    trace_id: str | None = None


type OutputType = (
    Literal[
        "bool",
        "float",
        "int",
        "str",
        "list[bool]",
        "list[float]",
        "list[int]",
        "list[str]",
    ]
    | dict[str, Any]
)
