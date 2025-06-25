"""Database models for user integrations with external services.

Terminology:
- Integration: A user's integration with an external service.
- Provider: An external service that can be integrated with. Defined by BaseOAuthProvider.


"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, NotRequired, Required, Self, TypedDict

from pydantic import UUID4, BaseModel, SecretStr
from sqlmodel import Field

from tracecat.identifiers import UserID, WorkspaceID
from tracecat.integrations.enums import IntegrationStatus


# Pydantic models for API responses
class IntegrationReadMinimal(BaseModel):
    """Response model for user integration."""

    id: UUID4
    provider_id: str
    status: IntegrationStatus
    is_expired: bool


class IntegrationRead(BaseModel):
    """Response model for user integration."""

    id: UUID4
    user_id: UserID | None = None
    token_type: str
    expires_at: datetime | None
    scope: str | None
    provider_id: str
    provider_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    status: IntegrationStatus
    is_expired: bool


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


class IntegrationOAuthConnect(BaseModel):
    """Request model for connecting an integration."""

    auth_url: str = Field(
        ...,
        description="The URL to redirect to for OAuth authentication",
    )
    provider_id: str = Field(
        ...,
        description="The provider that the user connected to",
    )


class IntegrationOAuthCallback(BaseModel):
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


class ProviderCategory(StrEnum):
    """Category of a provider."""

    AUTH = "auth"
    COMMUNICATION = "communication"
    CLOUD = "cloud"
    MONITORING = "monitoring"
    ALERTING = "alerting"
    OTHER = "other"


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
    categories: list[ProviderCategory] = Field(
        default_factory=list,
        description="Categories of the provider (e.g., auth, communication)",
    )
    features: list[str] = Field(
        default_factory=list,
        description="List of features provided by this integration",
    )
    setup_steps: list[str] = Field(
        default_factory=list,
        description="Step-by-step instructions for setting up the provider",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this provider is available for use",
    )
    api_docs_url: str | None = Field(
        default=None, description="URL to API documentation"
    )
    setup_guide_url: str | None = Field(default=None, description="URL to setup guide")
    troubleshooting_url: str | None = Field(
        default=None, description="URL to troubleshooting documentation"
    )


class OAuthState(BaseModel):
    """Data class for OAuth state."""

    workspace_id: WorkspaceID
    user_id: UserID
    state: uuid.UUID

    @classmethod
    def from_state(cls, state: str) -> Self:
        """Create an OAuthState from a state string."""
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
