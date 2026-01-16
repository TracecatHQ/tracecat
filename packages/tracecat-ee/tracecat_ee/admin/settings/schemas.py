"""Platform settings schemas."""

from __future__ import annotations

from pydantic import BaseModel


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
