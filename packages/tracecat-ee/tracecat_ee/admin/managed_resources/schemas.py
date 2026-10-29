"""Platform admin managed resources schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

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
