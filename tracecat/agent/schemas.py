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

from tracecat.agent.types import AgentConfig, DeferredToolResults
from tracecat.chat.schemas import ChatMessage


class DefaultModelSelection(BaseModel):
    """Canonical default-model selection for an organization."""

    catalog_id: uuid.UUID
    model_name: str = Field(..., min_length=1, max_length=500)
    model_provider: str = Field(..., min_length=1, max_length=120)
    custom_provider_id: uuid.UUID | None = Field(default=None)


class DefaultModelSelectionUpdate(BaseModel):
    """Payload for updating the organization's default model selection."""

    catalog_id: uuid.UUID


class RunAgentArgs(BaseModel):
    # extra="ignore" keeps in-flight workflow history replayable after the
    # legacy ``use_workspace_credentials`` field was removed: Temporal
    # stores the old shape in history and Pydantic will silently drop the
    # stale key during deserialization.
    model_config = ConfigDict(extra="ignore")

    user_prompt: str
    """User prompt for the agent."""
    session_id: uuid.UUID
    """Session ID for the agent execution."""
    active_stream_id: uuid.UUID | None = None
    """Per-turn stream id (Redis key suffix). Minted at the HTTP layer at turn
    start and pinned here so the worker producer writes to the same per-turn key
    the reader joins. None for legacy executions that predate per-turn keys."""
    curr_run_id: uuid.UUID | None = None
    """Workflow run id for this turn. Pinned here so the producer tags persisted
    history rows with it, letting mid-turn loads hide the active run's partial
    rows. None for legacy executions."""
    config: AgentConfig | None = None
    """Configuration for the agent. Required if preset_slug is not provided."""
    preset_slug: str | None = None
    """Slug for the preset configuration (if using a preset)."""
    preset_version: int | None = None
    """Optional preset version number to pin for this execution."""
    max_requests: int | None = None
    """Maximum number of requests for the agent."""
    max_tool_calls: int | None = None
    """Maximum number of tool calls for the agent."""
    timeout_seconds: int | None = None
    """Maximum active runtime for this run, clamped to the deployment
    ceiling; None inherits the default."""
    deferred_tool_results: DeferredToolResults | None = None
    """Results for deferred tool calls from a previous run (CE handshake)."""
    is_continuation: bool = False
    """If True, do not emit a new user message; continue prior run with deferred results."""

    @model_validator(mode="after")
    def validate_config_or_preset(self) -> RunAgentArgs:
        """Ensure either config or preset_slug is provided."""
        if self.config is None and self.preset_slug is None:
            raise ValueError("Either 'config' or 'preset_slug' must be provided")
        if self.preset_version is not None and self.preset_slug is None:
            raise ValueError("'preset_version' requires 'preset_slug'")
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
    catalog_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional catalog row backing this model selection. Populated "
            "for v2 org-scoped cloud/custom catalog rows; left ``None`` for "
            "platform (built-in) models that resolve credentials via "
            "``agent-{provider}-credentials``."
        ),
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


class RunUsage(BaseModel):
    """LLM usage associated with an agent run."""

    requests: int = 0
    """Number of requests made to the LLM API."""

    tool_calls: int = 0
    """Number of tool calls executed during the run."""

    input_tokens: int = 0
    """Total number of input tokens."""

    output_tokens: int = 0
    """Total number of output tokens."""


class AgentOutput(BaseModel):
    output: Any
    message_history: list[ChatMessage] | None = None
    duration: float
    usage: RunUsage | None = None
    session_id: uuid.UUID


class ToolFilters(BaseModel):
    actions: list[str] | None = None
    namespaces: list[str] | None = None
