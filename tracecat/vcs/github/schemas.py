"""GitHub App data models for workflow store."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, SecretBytes, SecretStr


class GitHubRepository(BaseModel):
    """GitHub repository information."""

    id: int
    name: str
    full_name: str
    private: bool
    default_branch: str = "main"


class GitHubInstallation(BaseModel):
    """GitHub App installation details."""

    id: int
    account_login: str
    account_type: str  # "Organization" or "User"
    target_type: str
    permissions: dict[str, str] = Field(default_factory=dict)
    repositories: list[GitHubRepository] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitHubAppConfig(BaseModel):
    """GitHub App configuration for workspace."""

    installation_id: int
    installation: GitHubInstallation | None = None

    # Enterprise-only fields
    app_id: str | None = None
    private_key_encrypted: SecretBytes | None = None

    # Managed-only fields - set by platform
    client_id: str | None = None
    webhook_secret: SecretStr | None = None

    # Access tracking
    accessible_repositories: list[str] = Field(default_factory=list)
    last_token_refresh: datetime | None = None


class GitHubAppCredentials(BaseModel):
    """GitHub App credentials for organization-level storage."""

    app_id: str = Field(..., description="GitHub App ID")
    private_key: SecretStr = Field(
        ..., description="GitHub App private key in PEM format"
    )
    webhook_secret: SecretStr | None = Field(
        None, description="GitHub App webhook secret"
    )
    client_id: str | None = Field(None, description="GitHub App client ID")
