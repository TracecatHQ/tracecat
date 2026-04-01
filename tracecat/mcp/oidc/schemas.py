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
    """Pending authorization request stored in Redis during login/org-selection redirect."""

    transaction_id: str
    authorize_params: dict[str, str]
    """Original ``/authorize`` query parameters to replay after login."""
    created_at: float
    bound_ip: str
    """SHA-256 hex digest of the requester's IP at transaction creation."""
