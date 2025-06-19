"""Database models for user integrations with external services."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import UUID4, BaseModel
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

    provider_id: str = Field(
        ...,
        description="The provider identifier",
    )
    client_id: str = Field(
        ...,
        description="OAuth client ID for the provider",
        min_length=1,
    )
    client_secret: str = Field(
        ...,
        description="OAuth client secret for the provider",
        min_length=1,
    )
    config: dict[str, Any] = Field(
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


@dataclass(slots=True)
class TokenResponse:
    """Data class for OAuth token response."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int = 3600
    scope: str = ""
    token_type: str = "Bearer"


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
