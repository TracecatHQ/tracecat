"""GitLab token credential models."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator


class GitLabTokenCredentials(BaseModel):
    """GitLab REST API credentials for organization-level workspace sync."""

    base_url: str = Field(
        default="https://gitlab.com",
        description="Base URL for GitLab.com or a self-managed GitLab instance.",
    )
    token: SecretStr = Field(
        ...,
        description=(
            "GitLab access token with api scope. Project and group tokens also "
            "require write_repository for Git sync pushes."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            raise ValueError("GitLab base URL is required.")
        if not cleaned.startswith(("https://", "http://")):
            raise ValueError("GitLab base URL must start with http:// or https://.")
        return cleaned
