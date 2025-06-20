"""Database models for user integrations with external services.

Terminology:
- Integration: A user's integration with an external service.
- Provider: An external service that can be integrated with. Defined by BaseOauthProvider.


"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NotRequired, Required, Self, TypedDict

from pydantic import UUID4, BaseModel, SecretStr
from sqlmodel import Field

from tracecat.identifiers import UserID, WorkspaceID
from tracecat.identifiers.workflow import WorkspaceIDShort


# Pydantic models for API responses
class IntegrationRead(BaseModel):
    """Response model for user integration."""

    id: UUID4
    workspace_id: WorkspaceIDShort
    user_id: UserID | None = None
    provider_id: str
    token_type: str
    expires_at: datetime | None
    scope: str | None
    provider_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at


class IntegrationUpdate(BaseModel):
    """Request model for updating an integration."""

    client_id: str = Field(
        ...,
        description="OAuth client ID for the provider",
        min_length=1,
    )
    client_secret: SecretStr = Field(
        ...,
        description="OAuth client secret for the provider",
        min_length=1,
    )
    provider_config: dict[str, Any] = Field(
        ...,
        description="Provider-specific configuration",
    )


class IntegrationOauthCallback(BaseModel):
    """Response for OAuth callback."""

    status: str = Field(
        default="connected",
        description="The status of the OAuth callback",
    )
    provider_id: str = Field(
        ...,
        description="The provider that the user connected to",
    )
    redirect_url: str = Field(
        ...,
        description="The URL to redirect to after the OAuth callback",
    )


class ProviderMetadata(BaseModel):
    """Metadata for a provider."""

    id: str = Field(..., description="Provider identifier")
    name: str = Field(..., description="Human-readable provider name")
    description: str = Field(..., description="Provider description")
    logo_url: str | None = Field(None, description="URL to provider logo")
    setup_instructions: str | None = Field(
        None, description="Setup instructions for the provider"
    )
    oauth_scopes: list[str] = Field(
        default_factory=list, description="Default OAuth scopes"
    )
    requires_config: bool = Field(
        False, description="Whether this provider requires additional configuration"
    )


class OauthState(BaseModel):
    """Data class for OAuth state."""

    workspace_id: WorkspaceID
    user_id: UserID
    state: uuid.UUID

    @classmethod
    def from_state(cls, state: str) -> Self:
        """Create an OauthState from a state string."""
        workspace_id, user_id, state = state.split(":")
        return cls(
            workspace_id=uuid.UUID(workspace_id),
            user_id=uuid.UUID(user_id),
            state=uuid.UUID(state),
        )


class OAuthProviderKwargs(TypedDict):
    """Kwargs for OAuth providers."""

    client_id: Required[str]
    client_secret: Required[str]
    scopes: NotRequired[list[str] | None]


class ProviderSchema(BaseModel):
    """Schema for a provider."""

    json_schema: dict[str, Any]


@dataclass(slots=True)
class TokenResponse:
    """Data class for OAuth token response."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_in: int = 3600
    scope: str = ""
    token_type: str = "Bearer"


@dataclass(slots=True)
class ProviderConfig:
    """Data class for integration client credentials."""

    client_id: str
    client_secret: SecretStr
    provider_config: dict[str, Any]
