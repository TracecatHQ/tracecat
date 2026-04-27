"""Schemas for agent model access control."""

from uuid import UUID

from pydantic import BaseModel


class AgentModelAccessCreate(BaseModel):
    """Enable a model for org or workspace."""

    catalog_id: UUID
    workspace_id: UUID | None = None


class AgentModelAccessRead(BaseModel):
    """Model access entry."""

    model_config = {"from_attributes": True}

    id: UUID
    organization_id: UUID
    workspace_id: UUID | None
    catalog_id: UUID


class AgentModelAccessListResponse(BaseModel):
    """List accessible models with pagination."""

    items: list[AgentModelAccessRead]
    next_cursor: str | None = None
