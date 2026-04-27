"""Schemas for agent model catalog."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentCatalogRead(BaseModel):
    """Single catalog model entry."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    custom_provider_id: UUID | None
    organization_id: UUID | None
    model_provider: str
    model_name: str
    model_metadata: dict[str, Any] | None


class AgentCatalogListResponse(BaseModel):
    """List catalog entries with pagination."""

    items: list[AgentCatalogRead]
    next_cursor: str | None = None


class CloudCatalogModelBase(BaseModel):
    """Shared, user-supplied fields for a cloud catalog entry.

    Capability and cost hints (``max_input_tokens``, ``input_cost_per_token``,
    ``supports_*``, ``mode``, etc.) are platform-derived and not accepted from
    the client; they are populated by platform bootstrap or discovery jobs.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None


class BedrockCatalogCreate(CloudCatalogModelBase):
    """Bedrock catalog entry. Requires exactly one of inference_profile_id or model_id."""

    model_provider: Literal["bedrock"]
    model_name: str = Field(min_length=1, max_length=500)
    inference_profile_id: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_one_model_ref(self) -> "BedrockCatalogCreate":
        has_profile = self.inference_profile_id is not None
        has_model_id = self.model_id is not None
        if has_profile == has_model_id:
            raise ValueError("Provide exactly one of inference_profile_id or model_id")
        return self


class AzureOpenAICatalogCreate(CloudCatalogModelBase):
    """Azure OpenAI catalog entry."""

    model_provider: Literal["azure_openai"]
    model_name: str = Field(min_length=1, max_length=500)
    deployment_name: str = Field(min_length=1)


class AzureAICatalogCreate(CloudCatalogModelBase):
    """Azure AI catalog entry."""

    model_provider: Literal["azure_ai"]
    model_name: str = Field(min_length=1, max_length=500)
    azure_ai_model_name: str = Field(min_length=1)


class VertexAICatalogCreate(CloudCatalogModelBase):
    """Vertex AI catalog entry."""

    model_provider: Literal["vertex_ai"]
    model_name: str = Field(min_length=1, max_length=500)
    vertex_model: str = Field(min_length=1)


AgentCatalogCreate = Annotated[
    BedrockCatalogCreate
    | AzureOpenAICatalogCreate
    | AzureAICatalogCreate
    | VertexAICatalogCreate,
    Field(discriminator="model_provider"),
]


class BedrockCatalogUpdate(CloudCatalogModelBase):
    model_provider: Literal["bedrock"]
    inference_profile_id: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_one_model_ref(self) -> "BedrockCatalogUpdate":
        has_profile = self.inference_profile_id is not None
        has_model_id = self.model_id is not None
        if has_profile == has_model_id:
            raise ValueError("Provide exactly one of inference_profile_id or model_id")
        return self


class AzureOpenAICatalogUpdate(CloudCatalogModelBase):
    model_provider: Literal["azure_openai"]
    deployment_name: str = Field(min_length=1)


class AzureAICatalogUpdate(CloudCatalogModelBase):
    model_provider: Literal["azure_ai"]
    azure_ai_model_name: str = Field(min_length=1)


class VertexAICatalogUpdate(CloudCatalogModelBase):
    model_provider: Literal["vertex_ai"]
    vertex_model: str = Field(min_length=1)


AgentCatalogUpdate = Annotated[
    BedrockCatalogUpdate
    | AzureOpenAICatalogUpdate
    | AzureAICatalogUpdate
    | VertexAICatalogUpdate,
    Field(discriminator="model_provider"),
]
