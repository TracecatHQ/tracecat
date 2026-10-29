"""API contracts for managed resources in a target workspace."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from tracecat.managed_resources.enums import (
    ManagedReferenceKind,
    ManagedRolloutKind,
    ManagedRolloutStatus,
)
from tracecat.workspace_sync.enums import SyncResourceType


class ManagedResourceRead(BaseModel):
    """One managed resource, for the "Managed by Tracecat" badge."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    source_id: str
    deployed_tag: str = Field(description="Package tag the resource came from.")
    deployed_commit_sha: str
    deployed_at: datetime = Field(description="When the last rollout wrote it.")


class DetachedCopyRead(BaseModel):
    """A customer-owned copy made by detaching a managed resource."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    original_local_id: uuid.UUID
    original_name: str
    detached_at: datetime


class ManagedResourceDetach(BaseModel):
    """Detach a managed resource into an editable copy named ``new_name``."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    new_name: str = Field(min_length=1, max_length=100)


type RolloutChangeKind = Literal["added", "modified", "deleted"]
type RegistryInstallAction = Literal["install", "present", "blocked"]


class RolloutChange(BaseModel):
    """One resource a rollout adds, modifies or deletes in the target."""

    change: RolloutChangeKind
    resource_type: SyncResourceType
    source_id: str
    name: str


class RolloutConflict(BaseModel):
    """An incoming managed resource whose name a customer resource already uses."""

    resource_type: SyncResourceType
    source_id: str
    name: str = Field(description="The name both resources share.")
    existing_local_id: uuid.UUID = Field(description="The customer's resource.")


class RolloutReference(BaseModel):
    """A model, MCP integration or secret store the target must match."""

    kind: ManagedReferenceKind
    source_key: str = Field(description="Source catalog id, integration id or store.")
    label: str = Field(description="What the source calls it.")


class RolloutDeleteWarning(BaseModel):
    """Customer data a delete destroys, shown before the FDE applies."""

    resource_type: SyncResourceType
    source_id: str
    name: str
    table_rows: int | None = Field(default=None, description="Rows in a deleted table.")
    cases_using: int | None = Field(
        default=None,
        description=(
            "Cases using a deleted case tag or dropdown. For a deleted case "
            "field, the number of cases in the workspace whose value is erased."
        ),
    )
    has_customer_values: bool = Field(
        default=False, description="A deleted secret or variable holds values."
    )
    referenced_by: list[str] = Field(
        default_factory=list,
        description="Customer workflows and presets that mention it by name.",
    )


class RolloutRegistryPlan(BaseModel):
    """What a rollout does with one custom registry repository."""

    origin: str
    version: str
    action: RegistryInstallAction
    reason: str | None = None


class RolloutDroppedSkill(BaseModel):
    """A customer skill binding dropped because the package now binds it."""

    preset: str
    skill: str


class RolloutPreview(BaseModel):
    """Everything an FDE reviews before applying a tag to one target."""

    changes: list[RolloutChange] = Field(default_factory=list)
    conflicts: list[RolloutConflict] = Field(default_factory=list)
    references: list[RolloutReference] = Field(default_factory=list)
    delete_warnings: list[RolloutDeleteWarning] = Field(default_factory=list)
    registry: list[RolloutRegistryPlan] = Field(default_factory=list)
    dropped_customer_skills: list[RolloutDroppedSkill] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class ManagedRolloutRead(BaseModel):
    """One rollout record for one target."""

    id: uuid.UUID
    target_id: uuid.UUID | None
    workspace_id: uuid.UUID
    kind: ManagedRolloutKind
    status: ManagedRolloutStatus
    tag: str | None
    commit_sha: str | None
    preview: RolloutPreview | None
    error: str | None
    created_at: datetime
    applied_at: datetime | None
