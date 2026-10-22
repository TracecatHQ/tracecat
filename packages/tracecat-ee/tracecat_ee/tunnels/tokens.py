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
# Only claims the gateway authorizes on are required. Log-only claims such as
# ``subject`` and ``action_ref`` stay optional so they can change without
# breaking gateways and executors on adjacent versions.
_REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "workspace_id")

GATEWAY_POLICY_TOKEN_ISSUER = "tracecat-tunnel-gateway"
GATEWAY_POLICY_TOKEN_AUDIENCE = "tracecat-tunnel-policy"
GATEWAY_POLICY_TOKEN_TTL_SECONDS = 60
_POLICY_REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp")


class TunnelTokenError(Exception):
    """Raised when a tunnel proxy token is missing, invalid, or for another tunnel."""


class TunnelTokenClaims(BaseModel):
    tunnel_id: uuid.UUID
    workspace_id: uuid.UUID
    subject: str | None = None
    """Who is using the tunnel, e.g. a workflow execution or an agent session."""
    action_ref: str | None = None


def generate_gateway_key() -> str:
    """Return a new random gateway key (256 bits, URL-safe)."""
    return secrets.token_urlsafe(32)


def mint_tunnel_token(
    *,
    gateway_key: str,
    tunnel_id: uuid.UUID,
    workspace_id: uuid.UUID,
    subject: str,
    action_ref: str | None = None,
    ttl_seconds: int = TUNNEL_TOKEN_DEFAULT_TTL_SECONDS,
) -> str:
    """Mint a proxy token scoped to one tunnel, workspace, and subject."""
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
        "subject": subject,
        # Older gateway images require this claim; a gateway image can be
        # pinned separately from the app, so keep sending it.
        "wf_exec_id": subject,
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


def mint_gateway_policy_token(*, gateway_key: str, tunnel_id: uuid.UUID) -> str:
    """Mint the token a gateway uses to fetch its tunnel's current policy."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": GATEWAY_POLICY_TOKEN_ISSUER,
        "aud": GATEWAY_POLICY_TOKEN_AUDIENCE,
        "sub": str(tunnel_id),
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=GATEWAY_POLICY_TOKEN_TTL_SECONDS)).timestamp()
        ),
    }
    return jwt.encode(payload, gateway_key, algorithm="HS256")


def unverified_policy_token_tunnel_id(token: str) -> uuid.UUID:
    """Read the tunnel ID from a policy token so its gateway key can be loaded.

    The token is not trusted until ``verify_gateway_policy_token`` succeeds.
    """
    try:
        payload = jwt.decode(
            token, options={"verify_signature": False}, algorithms=["HS256"]
        )
        return uuid.UUID(str(payload["sub"]))
    except (PyJWTError, KeyError, ValueError) as exc:
        raise TunnelTokenError("Invalid gateway policy token") from exc


def verify_gateway_policy_token(
    token: str, *, gateway_key: str, tunnel_id: uuid.UUID
) -> None:
    """Verify a policy token was signed with this tunnel's current gateway key."""
    try:
        payload = jwt.decode(
            token,
            gateway_key,
            algorithms=["HS256"],
            audience=GATEWAY_POLICY_TOKEN_AUDIENCE,
            issuer=GATEWAY_POLICY_TOKEN_ISSUER,
            options={"require": list(_POLICY_REQUIRED_CLAIMS)},
        )
    except PyJWTError as exc:
        raise TunnelTokenError("Invalid gateway policy token") from exc
    if payload["exp"] - payload["iat"] > GATEWAY_POLICY_TOKEN_TTL_SECONDS:
        raise TunnelTokenError("Gateway policy token lifetime is too long")
    if payload["sub"] != str(tunnel_id):
        raise TunnelTokenError("Gateway policy token is for a different tunnel")
