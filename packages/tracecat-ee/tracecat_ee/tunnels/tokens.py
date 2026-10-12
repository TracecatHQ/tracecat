"""Short-lived proxy tokens that let one action run use one tunnel gateway.

Tokens are HS256 JWTs signed with the tunnel's own gateway key, so a gateway
can only verify (and a leaked key can only forge) tokens for its own tunnel.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, ValidationError

TUNNEL_TOKEN_ISSUER = "tracecat-executor"
TUNNEL_TOKEN_AUDIENCE = "tracecat-tunnel-gateway"
TUNNEL_TOKEN_DEFAULT_TTL_SECONDS = 300
TUNNEL_TOKEN_MAX_TTL_SECONDS = 900
_REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "workspace_id", "wf_exec_id")


class TunnelTokenError(Exception):
    """Raised when a tunnel proxy token is missing, invalid, or for another tunnel."""


class TunnelTokenClaims(BaseModel):
    tunnel_id: uuid.UUID
    workspace_id: uuid.UUID
    wf_exec_id: str
    action_ref: str | None = None


def generate_gateway_key() -> str:
    """Return a new random gateway key (256 bits, URL-safe)."""
    return secrets.token_urlsafe(32)


def mint_tunnel_token(
    *,
    gateway_key: str,
    tunnel_id: uuid.UUID,
    workspace_id: uuid.UUID,
    wf_exec_id: str,
    action_ref: str | None = None,
    ttl_seconds: int = TUNNEL_TOKEN_DEFAULT_TTL_SECONDS,
) -> str:
    """Mint a proxy token scoped to one tunnel, workspace, and workflow execution."""
    if not 0 < ttl_seconds <= TUNNEL_TOKEN_MAX_TTL_SECONDS:
        raise ValueError(
            f"ttl_seconds must be between 1 and {TUNNEL_TOKEN_MAX_TTL_SECONDS}"
        )
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": TUNNEL_TOKEN_ISSUER,
        "aud": TUNNEL_TOKEN_AUDIENCE,
        "sub": str(tunnel_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "workspace_id": str(workspace_id),
        "wf_exec_id": wf_exec_id,
    }
    if action_ref is not None:
        payload["action_ref"] = action_ref
    return jwt.encode(payload, gateway_key, algorithm="HS256")


def verify_tunnel_token(
    token: str,
    *,
    gateway_key: str,
    tunnel_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> TunnelTokenClaims:
    """Verify a proxy token for this gateway's tunnel and workspace."""
    try:
        payload = jwt.decode(
            token,
            gateway_key,
            algorithms=["HS256"],
            audience=TUNNEL_TOKEN_AUDIENCE,
            issuer=TUNNEL_TOKEN_ISSUER,
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except PyJWTError as exc:
        raise TunnelTokenError("Invalid tunnel token") from exc
    if payload["exp"] - payload["iat"] > TUNNEL_TOKEN_MAX_TTL_SECONDS:
        raise TunnelTokenError("Tunnel token lifetime is too long")
    try:
        claims = TunnelTokenClaims.model_validate(
            {**payload, "tunnel_id": payload["sub"]}
        )
    except ValidationError as exc:
        raise TunnelTokenError("Tunnel token claims are invalid") from exc
    if claims.tunnel_id != tunnel_id or claims.workspace_id != workspace_id:
        raise TunnelTokenError("Tunnel token is for a different tunnel")
    return claims
