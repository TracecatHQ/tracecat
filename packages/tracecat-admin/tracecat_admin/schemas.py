"""Pydantic schemas for CLI responses."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class UserRead(BaseModel):
    """User response schema."""

    id: uuid.UUID
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    role: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class OrgRead(BaseModel):
    """Organization response schema."""

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


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


class RegistrySettingsRead(BaseModel):
    """Platform registry settings."""

    git_repo_url: str | None = None
    git_repo_package_name: str | None = None
    git_allowed_domains: set[str] | None = None


class RegistryVersionPromoteResponse(BaseModel):
    """Response from promoting a registry version."""

    repository_id: uuid.UUID
    origin: str
    previous_version_id: uuid.UUID | None
    current_version_id: uuid.UUID
    version: str


# Org Registry Schemas


class OrgRegistryRepositoryRead(BaseModel):
    """Organization registry repository response."""

    id: uuid.UUID
    origin: str
    last_synced_at: datetime | None = None
    commit_sha: str | None = None
    current_version_id: uuid.UUID | None = None


class OrgRegistrySyncResponse(BaseModel):
    """Organization registry sync response."""

    success: bool
    repository_id: uuid.UUID
    origin: str
    version: str | None = None
    commit_sha: str | None = None
    actions_count: int | None = None
    forced: bool = False
    skipped: bool = False
    message: str | None = None


class OrgRegistryVersionPromoteResponse(BaseModel):
    """Response from promoting an organization registry version."""

    repository_id: uuid.UUID
    origin: str
    previous_version_id: uuid.UUID | None
    previous_version: str | None
    current_version_id: uuid.UUID
    current_version: str


class OrgInviteResponse(BaseModel):
    """Response from inviting a user to an organization."""

    invitation_id: uuid.UUID
    email: str
    role: str
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    org_created: bool
    magic_link: str
    email_sent: bool
    email_error: str | None = None
