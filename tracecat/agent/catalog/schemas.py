"""Schemas for agent model catalog."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
