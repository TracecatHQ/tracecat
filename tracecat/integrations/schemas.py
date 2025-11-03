"""Database models for user integrations with external services.

Terminology:
- Integration: A user's integration with an external service.
- Provider: An external service that can be integrated with. Defined by BaseOAuthProvider.
"""

import uuid
from datetime import datetime
from typing import Any, Self, TypedDict

from pydantic import UUID4, BaseModel, SecretStr
from sqlmodel import Field

from tracecat.identifiers import UserID, WorkspaceID
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType


# Pydantic models for API responses
class IntegrationReadMinimal(BaseModel):
    """Response model for user integration."""

    id: UUID4
    provider_id: str
    status: IntegrationStatus
    is_expired: bool


class IntegrationRead(BaseModel):
    """Response model for user integration."""

    # Core identification and timestamps
    id: UUID4
    created_at: datetime
    updated_at: datetime
    user_id: UserID | None = None

    # Provider information
    provider_id: str
    authorization_endpoint: str | None = Field(
        default=None,
        description="OAuth authorization endpoint configured for this integration.",
    )
    token_endpoint: str | None = Field(
        default=None,
        description="OAuth token endpoint configured for this integration.",
    )

    # OAuth token details
    token_type: str
    expires_at: datetime | None

    # OAuth credentials
    client_id: str | None = Field(
        default=None,
        description="OAuth client ID for the provider",
    )

    # OAuth scopes
    granted_scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes granted for this integration",
    )
    requested_scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes requested by user for this integration",
    )

    # Integration state
    status: IntegrationStatus
    is_expired: bool


class IntegrationUpdate(BaseModel):
    """Request model for updating an integration."""

    # Additional identifier
    grant_type: OAuthGrantType = Field(
        ...,
        description="OAuth grant type for this integration",
    )

    # Updateable fields
    client_id: str | None = Field(
        default=None,
        description="OAuth client ID for the provider",
        min_length=1,
    )
    client_secret: SecretStr | None = Field(
        default=None,
        description="OAuth client secret for the provider",
        min_length=1,
    )
    authorization_endpoint: str | None = Field(
        default=None,
        description="OAuth authorization endpoint URL. Overrides provider defaults when set.",
        min_length=8,
    )
    token_endpoint: str | None = Field(
        default=None,
        description="OAuth token endpoint URL. Overrides provider defaults when set.",
        min_length=8,
    )
    scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes to request for this integration",
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


class IntegrationTestConnectionResponse(BaseModel):
    """Response for testing integration connection."""

    success: bool = Field(
        ...,
        description="Whether the connection test was successful",
    )
    provider_id: str = Field(
        ...,
        description="The provider that was tested",
    )
    message: str = Field(
        ...,
        description="Message describing the test result",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the test failed",
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
    requires_config: bool = Field(
        False, description="Whether this provider requires additional configuration"
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


class ProviderScopes(BaseModel):
    """Scope metadata for a provider."""

    default: list[str] = Field(
        ...,
        description="Default scopes for this provider.",
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


class OAuthProviderKwargs(TypedDict, total=False):
    """Kwargs for OAuth providers."""

    client_id: str
    client_secret: str
    scopes: list[str] | None
    authorization_endpoint: str
    token_endpoint: str


class ProviderKey(BaseModel):
    """Key for a provider that uniquely identifies it."""

    id: str
    grant_type: OAuthGrantType

    def __str__(self) -> str:
        return f"{self.id} ({self.grant_type.value})"

    def __hash__(self) -> int:
        return hash((self.id, self.grant_type))


class ProviderSchema(BaseModel):
    """Schema for a provider."""

    json_schema: dict[str, Any]


class ProviderConfig(BaseModel):
    """Data class for integration client credentials."""

    client_id: str | None = None
    client_secret: SecretStr | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    scopes: list[str] | None = None


class ProviderReadMinimal(BaseModel):
    id: str
    name: str
    description: str
    requires_config: bool
    integration_status: IntegrationStatus
    enabled: bool
    grant_type: OAuthGrantType


class ProviderRead(BaseModel):
    grant_type: OAuthGrantType
    metadata: ProviderMetadata
    scopes: ProviderScopes
    config_schema: ProviderSchema
    integration_status: IntegrationStatus
    default_authorization_endpoint: str | None = None
    default_token_endpoint: str | None = None
    authorization_endpoint_help: str | list[str] | None = None
    token_endpoint_help: str | list[str] | None = None
    # Only applicable to AuthorizationCodeOAuthProvider
    redirect_uri: str | None = None
