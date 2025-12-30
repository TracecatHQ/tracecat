"""JWT tokens for MCP server authentication.

Provides token minting and verification for authenticating requests
between the proxy MCP server and the trusted HTTP MCP server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, ValidationError

from tracecat import config
from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.auth.types import Role

MCP_TOKEN_ISSUER = "tracecat-mcp"
MCP_TOKEN_AUDIENCE = "tracecat-mcp-server"
MCP_TOKEN_SUBJECT = "tracecat-mcp-client"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "role", "allowed_actions")


class MCPTokenClaims(BaseModel):
    """Claims extracted from a verified MCP token."""

    role: Role
    run_id: str | None = None
    session_id: str | None = None
    allowed_actions: dict[str, MCPToolDefinition]


def mint_mcp_token(
    *,
    role: Role,
    allowed_actions: dict[str, MCPToolDefinition],
    run_id: str | None = None,
    session_id: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed MCP JWT containing role and allowed actions.

    Args:
        role: The Role for authorization context
        allowed_actions: Dict mapping action names to their tool definitions
        run_id: Optional run identifier
        session_id: Optional session identifier
        ttl_seconds: Token TTL in seconds (defaults to executor token TTL)

    Returns:
        Signed JWT string
    """
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    now = datetime.now(UTC)
    ttl = ttl_seconds or config.TRACECAT__EXECUTOR_TOKEN_TTL_SECONDS

    # Serialize tool definitions to JSON-compatible format
    allowed_actions_serialized = {
        name: defn.model_dump(mode="json") for name, defn in allowed_actions.items()
    }

    payload: dict[str, Any] = {
        "iss": MCP_TOKEN_ISSUER,
        "aud": MCP_TOKEN_AUDIENCE,
        "sub": MCP_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "role": role.model_dump(mode="json"),
        "allowed_actions": allowed_actions_serialized,
    }
    if run_id:
        payload["run_id"] = run_id
    if session_id:
        payload["session_id"] = session_id

    return jwt.encode(payload, config.TRACECAT__SERVICE_KEY, algorithm="HS256")


def verify_mcp_token(token: str) -> MCPTokenClaims:
    """Verify MCP JWT and return extracted claims.

    Args:
        token: The JWT string to verify

    Returns:
        MCPTokenClaims containing role and allowed_actions

    Raises:
        ValueError: If token is invalid or missing required claims
    """
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    try:
        payload = jwt.decode(
            token,
            config.TRACECAT__SERVICE_KEY,
            algorithms=["HS256"],
            audience=MCP_TOKEN_AUDIENCE,
            issuer=MCP_TOKEN_ISSUER,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except PyJWTError as exc:
        raise ValueError("Invalid MCP token") from exc

    if payload.get("sub") != MCP_TOKEN_SUBJECT:
        raise ValueError("Invalid MCP token subject")

    role_payload = payload.get("role")
    if role_payload is None:
        raise ValueError("MCP token missing role claim")

    allowed_actions_payload = payload.get("allowed_actions")
    if allowed_actions_payload is None:
        raise ValueError("MCP token missing allowed_actions claim")

    try:
        role = Role.model_validate(role_payload)
    except ValidationError as exc:
        raise ValueError("MCP token role claim is invalid") from exc

    # Deserialize tool definitions
    try:
        allowed_actions = {
            name: MCPToolDefinition.model_validate(defn)
            for name, defn in allowed_actions_payload.items()
        }
    except ValidationError as exc:
        raise ValueError("MCP token allowed_actions claim is invalid") from exc

    return MCPTokenClaims(
        role=role,
        run_id=payload.get("run_id"),
        session_id=payload.get("session_id"),
        allowed_actions=allowed_actions,
    )
