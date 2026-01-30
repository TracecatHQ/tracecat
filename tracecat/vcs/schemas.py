"""API models for VCS integrations."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr

from tracecat.vcs.github.manifest import GitHubAppManifest


class GitHubAppInstallRequest(BaseModel):
    """Request to set GitHub App installation ID for workspace."""

    installation_id: int


class GitHubAppCredentialsRequest(BaseModel):
    """Request to register or update GitHub App credentials."""

    app_id: str = Field(..., description="GitHub App ID")
    private_key: SecretStr = Field(
        ..., description="GitHub App private key in PEM format"
    )
    webhook_secret: SecretStr | None = Field(
        None, description="GitHub App webhook secret"
    )
    client_id: str | None = Field(None, description="GitHub App client ID")


class GitHubAppCredentialsStatus(BaseModel):
    """Status of GitHub App credentials."""

    exists: bool
    app_id: str | None = None
    has_webhook_secret: bool = False
    webhook_secret_preview: str | None = None
    client_id: str | None = None
    created_at: str | None = None


class GitHubAppManifestResponse(BaseModel):
    """GitHub App manifest response."""

    manifest: GitHubAppManifest
    instructions: list[str]
