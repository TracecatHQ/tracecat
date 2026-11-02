"""Pydantic models for the internal OIDC issuer."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class AuthCodeData(BaseModel):
    """Authorization code stored in Redis, keyed by the opaque code string."""

    code: str
    user_id: uuid.UUID
    email: str
    organization_id: uuid.UUID
    """Primary org, issued as the ``organization_id`` claim."""
    organization_ids: list[uuid.UUID] = Field(default_factory=list)
    """Every org the user granted to this connection. Empty means only
    ``organization_id`` (codes minted before multi-org support)."""
    is_platform_superuser: bool
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str = Field(default="S256")
    scope: str
    resource: str
    """OAuth ``resource`` parameter — becomes the access token ``aud``."""
    nonce: str | None = None
    created_at: float
    bound_ip: str
    """SHA-256 hex digest of the requester's IP at authorize time."""


class ResumeTransaction(BaseModel):
    """Pending authorization request stored in Redis during login redirect."""

    transaction_id: str
    authorize_params: dict[str, str]
    """Original ``/authorize`` query parameters to replay after login."""
    created_at: float
    bound_ip: str
    """SHA-256 hex digest of the requester's IP at transaction creation."""


class OrganizationSelectionTransaction(BaseModel):
    """Pending authorization waiting for a multi-org user to pick organizations."""

    transaction_id: str
    user_id: uuid.UUID
    active_organization_id: uuid.UUID
    """The user's current org; it stays the primary org when selected."""
    authorize_params: dict[str, str]
    """Validated ``/authorize`` parameters used to issue the code."""
    created_at: float
    bound_ip: str
    """SHA-256 hex digest of the requester's IP at transaction creation."""


class OrganizationSelectionOption(BaseModel):
    """An organization shown on the MCP organization selection page."""

    id: uuid.UUID
    name: str
    slug: str


class OrganizationSelectionDetails(BaseModel):
    """Response for ``GET /authorize/organizations``."""

    organizations: list[OrganizationSelectionOption]
    active_organization_id: uuid.UUID


class OrganizationSelectionRequest(BaseModel):
    """Request body for ``POST /authorize/organizations``."""

    txn: str
    organization_ids: list[uuid.UUID] = Field(default_factory=list)
    decision: Literal["allow", "deny"] = "allow"


class OrganizationSelectionResult(BaseModel):
    """Where the browser goes next to finish the OAuth flow."""

    redirect_url: str


class RefreshTokenMetadata(BaseModel):
    """Encrypted metadata blob persisted with each refresh token.

    Holds the session context required to mint new access tokens on rotation
    without re-resolving the user/org. Stored as a Fernet-encrypted JSON blob
    in the ``mcp_refresh_token.encrypted_metadata`` column.
    """

    email: str
    is_platform_superuser: bool
    scope: str
    resource: str
    """OAuth ``resource`` parameter — becomes the access token ``aud``."""
    organization_ids: list[uuid.UUID] | None = None
    """Orgs the user granted at consent. ``None`` means only the row's
    ``organization_id`` (tokens issued before multi-org support)."""


class RefreshTokenContext(BaseModel):
    """Hydrated refresh token returned from the rotation flow."""

    family_id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    client_id: str
    metadata: RefreshTokenMetadata
