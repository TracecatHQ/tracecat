"""Platform settings schemas."""

from __future__ import annotations

from pydantic import BaseModel

from tracecat.settings.schemas import AuditSettingsRead, AuditSettingsUpdate


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


class PlatformAuditSettingsRead(AuditSettingsRead):
    """Platform audit settings response."""


class PlatformAuditSettingsUpdate(AuditSettingsUpdate):
    """Update platform audit settings."""
