"""Schemas for custom LLM provider management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentCustomProviderCreate(BaseModel):
    """Create custom LLM provider."""

    display_name: str = Field(..., max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key_header: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None)
    custom_headers: dict[str, str] | None = Field(default=None)


class AgentCustomProviderRead(BaseModel):
    """Read custom provider."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    display_name: str
    base_url: str | None
    api_key_header: str | None
    discovery_status: str
    last_refreshed_at: datetime | None


class AgentCustomProviderUpdate(BaseModel):
    """Update custom provider."""

    display_name: str | None = None
    base_url: str | None = None
    api_key_header: str | None = None
    api_key: str | None = None
    custom_headers: dict[str, str] | None = None


class AgentCustomProviderListResponse(BaseModel):
    """List response with pagination."""

    items: list[AgentCustomProviderRead]
    next_cursor: str | None = None
