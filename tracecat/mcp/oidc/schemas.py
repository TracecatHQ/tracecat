"""Pydantic models for the internal OIDC issuer."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AuthCodeData(BaseModel):
    """Authorization code stored in Redis, keyed by the opaque code string."""

    code: str
    user_id: uuid.UUID
    email: str
    organization_id: uuid.UUID
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


class RefreshTokenContext(BaseModel):
    """Hydrated refresh token returned from the rotation flow."""

    family_id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    client_id: str
    metadata: RefreshTokenMetadata
