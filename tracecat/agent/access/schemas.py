"""Schemas for agent model access control."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from tracecat.agent.catalog.schemas import WorkspaceAgentModelCandidate


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


class WorkspaceAgentModelAccessRead(BaseModel):
    """A workspace's model subset and the organization models it can pick from."""

    inherit_all: bool = Field(
        description="True when the workspace has no subset and uses every organization-enabled model.",
    )
    catalog_ids: list[UUID] = Field(
        description="Catalog IDs in the workspace's subset. Empty when inherit_all is true.",
    )
    candidates: list[WorkspaceAgentModelCandidate] = Field(
        description=(
            "Models enabled for the organization. Provider URLs are left out: "
            "they can carry credentials, and a candidate may sit outside the "
            "workspace's subset."
        ),
    )


class WorkspaceAgentModelAccessUpdate(BaseModel):
    """Replace a workspace's model subset, or clear it to inherit every org model."""

    inherit_all: bool
    catalog_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_subset(self) -> Self:
        if self.inherit_all and self.catalog_ids:
            raise ValueError("catalog_ids must be empty when inherit_all is true")
        if not self.inherit_all and not self.catalog_ids:
            raise ValueError(
                "Select at least one model, or inherit the organization's models"
            )
        return self
