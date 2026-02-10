"""Organization management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from tracecat.core.schemas import Schema


class OrgCreate(Schema):
    """Create organization request."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=63, pattern=r"^[a-z0-9-]+$")


class OrgUpdate(Schema):
    """Update organization request."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=63, pattern=r"^[a-z0-9-]+$")
    is_active: bool | None = None


class OrgRead(Schema):
    """Organization response."""

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


# Org Domain Schemas


class OrgDomainCreate(Schema):
    """Create organization domain request."""

    domain: str = Field(..., min_length=1, max_length=255)
    is_primary: bool = False


class OrgDomainUpdate(Schema):
    """Update organization domain request."""

    is_primary: bool | None = None
    is_active: bool | None = None


class OrgDomainRead(Schema):
    """Organization domain response."""

    id: uuid.UUID
    organization_id: uuid.UUID
    domain: str
    normalized_domain: str
    is_primary: bool
    is_active: bool
    verified_at: datetime | None = None
    verification_method: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# Org Registry Schemas


class OrgRegistryRepositoryRead(Schema):
    """Organization registry repository response."""

    id: uuid.UUID
    origin: str
    last_synced_at: datetime | None = None
    commit_sha: str | None = None
    current_version_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class OrgRegistryVersionRead(Schema):
    """Organization registry version response."""

    id: uuid.UUID
    repository_id: uuid.UUID
    version: str
    commit_sha: str | None = None
    tarball_uri: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OrgRegistrySyncRequest(Schema):
    """Organization registry sync request."""

    force: bool = Field(
        default=False,
        description="Force sync by deleting the existing version first",
    )


class OrgRegistrySyncResponse(Schema):
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


class OrgRegistryVersionPromoteResponse(Schema):
    """Response from promoting an organization registry version."""

    repository_id: uuid.UUID
    origin: str
    previous_version_id: uuid.UUID | None
    previous_version: str | None
    current_version_id: uuid.UUID
    current_version: str
