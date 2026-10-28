"""Platform settings schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from tracecat.settings.schemas import (
    AuditSettingsRead,
    AuditSettingsUpdate,
    SecuritySettingsRead,
    SecuritySettingsUpdate,
)
from tracecat.workspace_sync.enums import VcsProvider


class PlatformRegistrySettingsRead(BaseModel):
    """Platform registry settings response."""

    git_repo_url: str | None = None
    git_repo_package_name: str | None = None
    git_allowed_domains: set[str] | None = None


class PlatformRegistrySettingsUpdate(BaseModel):
    """Update platform registry settings."""

    git_repo_url: str | None = None
    git_repo_package_name: str | None = None
    git_allowed_domains: set[str] | None = None


class PlatformManagedResourcesSourceRead(BaseModel):
    """The workspace managed resources are published from.

    All fields other than ``configured`` are null when no source is set.
    """

    configured: bool = Field(default=False)
    workspace_id: uuid.UUID | None = Field(default=None)
    organization_id: uuid.UUID | None = Field(default=None)
    git_provider: VcsProvider | None = Field(default=None)
    git_repo_url: str | None = Field(default=None)


class PlatformManagedResourcesSourceUpdate(BaseModel):
    """Set the workspace managed resources are published from.

    The workspace must have a Git sync provider and repository URL.
    """

    workspace_id: uuid.UUID


class PlatformAuditSettingsRead(AuditSettingsRead):
    """Platform audit settings response."""


class PlatformAuditSettingsUpdate(AuditSettingsUpdate):
    """Update platform audit settings."""


class PlatformSecuritySettingsRead(SecuritySettingsRead):
    """Platform admin IP allowlist settings response."""


class PlatformSecuritySettingsUpdate(SecuritySettingsUpdate):
    """Update the platform admin IP allowlist.

    Applies only to the platform admin API, not to organizations.
    """
