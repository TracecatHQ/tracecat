"""Platform admin managed resources schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from tracecat.managed_resources.enums import ManagedReferenceKind, ManagedTargetStatus
from tracecat.workspace_sync.enums import VcsProvider


class ManagedSourceRead(BaseModel):
    """The workspace managed resources are published from, and its Git binding."""

    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    git_provider: VcsProvider
    git_repo_url: str


class ManagedSourceSettingRead(BaseModel):
    """The managed resources source setting; ``source`` is null when unset."""

    source: ManagedSourceRead | None = Field(default=None)


class ManagedSourceUpdate(BaseModel):
    """Set the managed resources source.

    The workspace must have a Git sync provider and repository URL; both are
    pinned with the setting.
    """

    workspace_id: uuid.UUID


class ManagedWorkspaceRead(BaseModel):
    """A workspace an FDE can assign as a target."""

    workspace_id: uuid.UUID
    workspace_name: str
    organization_id: uuid.UUID
    organization_name: str
    is_source: bool
    target_id: uuid.UUID | None = Field(
        default=None, description="Set when the workspace is already a target."
    )


class ManagedTargetRead(BaseModel):
    """A customer workspace that receives the package."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str
    organization_id: uuid.UUID
    organization_name: str
    pinned_tag: str
    last_applied_tag: str | None
    last_rollout_status: ManagedTargetStatus
    last_rollout_at: datetime | None
    assigned_at: datetime


class ManagedTargetCreate(BaseModel):
    """Assign a workspace as a target, pinned to a tag."""

    workspace_id: uuid.UUID
    pinned_tag: str = Field(min_length=1, max_length=255)


class ManagedTargetUpdate(BaseModel):
    """Move a target's pin to another tag; a rollout applies it."""

    pinned_tag: str = Field(min_length=1, max_length=255)


class ManagedReferenceMatchItem(BaseModel):
    """The target's equivalent of one source model, MCP integration or store."""

    reference_kind: ManagedReferenceKind
    source_key: str = Field(min_length=1, max_length=255)
    resolved_id: uuid.UUID | None = Field(
        default=None,
        description="Target resource id. Null leaves a secret store unlinked.",
    )


class ManagedReferenceMatchesUpdate(BaseModel):
    """Replace a target's saved reference matches."""

    matches: list[ManagedReferenceMatchItem] = Field(default_factory=list)


class ManagedRolloutPreviewRequest(BaseModel):
    """Preview a tag for several targets at once."""

    target_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    tag: str | None = Field(
        default=None,
        description="Tag to preview. ``None`` uses each target's pinned tag.",
    )


class ManagedRolloutApplyRequest(BaseModel):
    """Apply previewed rollouts; only ready ones are applied."""

    rollout_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class ManagedReleaseCreate(BaseModel):
    """Cut a release tag at the HEAD of the source repository's base branch."""

    tag: str = Field(min_length=1, max_length=255)
