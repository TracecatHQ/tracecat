"""Admin registry schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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
    is_current: bool = False
    artifacts_ready: bool = False
    workflow_definition_count: int = 0
    in_use: bool = False

    model_config = {"from_attributes": True}


class RegistryVersionPromoteResponse(BaseModel):
    """Response from promoting a registry version."""

    repository_id: uuid.UUID
    origin: str
    previous_version_id: uuid.UUID | None
    current_version_id: uuid.UUID
    version: str


class RegistryArtifactsBackfillStartRequest(BaseModel):
    """Request to start an artifact backfill workflow for selected versions."""

    version_ids: list[uuid.UUID] = Field(..., min_length=1)


class RegistryArtifactsBackfillStartResponse(BaseModel):
    """Response after scheduling an artifact backfill workflow."""

    workflow_id: str
    requested_count: int
