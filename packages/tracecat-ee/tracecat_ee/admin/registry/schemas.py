"""Admin registry schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class RepositorySyncResult(BaseModel):
    """Result of syncing a single repository."""

    repository_id: uuid.UUID
    repository_name: str
    success: bool
    error: str | None = None
    version: str | None = None
    actions_count: int | None = None


class RegistrySyncResponse(BaseModel):
    """Response from sync operation."""

    success: bool
    synced_at: datetime
    repositories: list[RepositorySyncResult]


class RepositoryStatus(BaseModel):
    """Status of a single repository."""

    id: uuid.UUID
    name: str
    origin: str
    last_synced_at: datetime | None
    commit_sha: str | None
    current_version_id: uuid.UUID | None = None


class RegistryStatusResponse(BaseModel):
    """Registry health status."""

    total_repositories: int
    last_sync_at: datetime | None
    repositories: list[RepositoryStatus]


class RegistryVersionRead(BaseModel):
    """Registry version details."""

    id: uuid.UUID
    repository_id: uuid.UUID
    version: str
    commit_sha: str | None
    tarball_uri: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RegistryVersionPromoteResponse(BaseModel):
    """Response from promoting a registry version."""

    repository_id: uuid.UUID
    origin: str
    previous_version_id: uuid.UUID | None
    current_version_id: uuid.UUID
    version: str
