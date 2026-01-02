"""JWT tokens for agent runtime authentication.

This module provides token minting and verification for authenticating requests
between the jailed agent runtime and trusted services:

1. MCP Token: For tool execution via the trusted MCP server
2. LLM Token: For LLM API calls via the LiteLLM gateway

These tokens are separate to provide isolation - a compromised MCP execution
path (which runs user code) cannot make arbitrary LLM calls, and vice versa.

Both tokens embed workspace_id so the jailed runtime never sees it directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, Field, ValidationError

from tracecat import config
from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.types import OutputType
from tracecat.auth.types import Role

# -----------------------------------------------------------------------------
# MCP Token (for tool execution)
# -----------------------------------------------------------------------------

MCP_TOKEN_ISSUER = "tracecat-agent-executor"
MCP_TOKEN_AUDIENCE = "tracecat-mcp-server"
MCP_TOKEN_SUBJECT = "tracecat-agent-runtime"
MCP_REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "role", "allowed_actions")


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

    This token is minted by the AgentExecutor (trusted) and passed to the
    jailed runtime. The runtime cannot decode or modify it - it's opaque.

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
            options={"require": list(MCP_REQUIRED_CLAIMS)},
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


# -----------------------------------------------------------------------------
# LLM Token (for LLM API calls)
# -----------------------------------------------------------------------------

LLM_TOKEN_ISSUER = "tracecat-agent-executor"
LLM_TOKEN_AUDIENCE = "tracecat-llm-gateway"
LLM_TOKEN_SUBJECT = "tracecat-agent-runtime"
LLM_REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "iat",
    "exp",
    "workspace_id",
    "session_id",
    "model",
)


class LLMTokenClaims(BaseModel):
    """Claims extracted from a verified LLM token.

    These claims are set by the AgentExecutor when minting the token
    and are immutable - the jailed runtime cannot modify them.
    """

    # Identity
    workspace_id: str = Field(..., description="Workspace UUID as string")
    session_id: str = Field(..., description="Agent session UUID as string")

    # Model configuration
    model: str = Field(..., description="The model to use for this run")

    # Model settings - passed through to LLM provider as-is
    # Supports: temperature, max_tokens, reasoning_effort, etc.
    model_settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Model-specific settings passed through to the LLM provider",
    )

    # Output type for structured outputs (response_format)
    output_type: OutputType | None = Field(
        default=None,
        description="Expected output type for structured outputs",
    )

    # Credential scope
    use_workspace_credentials: bool = Field(
        default=False,
        description="If True, use workspace-level credentials; otherwise org-level",
    )


def mint_llm_token(
    *,
    workspace_id: str,
    session_id: str,
    model: str,
    model_settings: dict[str, Any] | None = None,
    output_type: OutputType | None = None,
    use_workspace_credentials: bool = False,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed LLM JWT for jailed agent runtime.

    This token is minted by the AgentExecutor (trusted) and passed to the
    jailed runtime. The runtime cannot decode or modify it - it's opaque.

    Args:
        workspace_id: The workspace UUID as string
        session_id: The agent session UUID as string
        model: The model to use for this run
        model_settings: Model-specific settings (temperature, max_tokens,
            reasoning_effort, etc.) passed through to LLM provider
        output_type: Expected output type for structured outputs
        use_workspace_credentials: Whether to use workspace-level creds
        ttl_seconds: Token TTL in seconds (defaults to executor token TTL)

    Returns:
        Signed JWT string
    """
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    now = datetime.now(UTC)
    ttl = ttl_seconds or config.TRACECAT__EXECUTOR_TOKEN_TTL_SECONDS

    payload: dict[str, Any] = {
        # Standard JWT claims
        "iss": LLM_TOKEN_ISSUER,
        "aud": LLM_TOKEN_AUDIENCE,
        "sub": LLM_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        # Identity claims
        "workspace_id": workspace_id,
        "session_id": session_id,
        # Model configuration
        "model": model,
        "model_settings": model_settings or {},
        # Credential scope
        "use_workspace_credentials": use_workspace_credentials,
    }

    # Only include output_type if provided
    if output_type is not None:
        payload["output_type"] = output_type

    return jwt.encode(payload, config.TRACECAT__SERVICE_KEY, algorithm="HS256")


def verify_llm_token(token: str) -> LLMTokenClaims:
    """Verify LLM JWT and return extracted claims.

    Args:
        token: The JWT string to verify

    Returns:
        LLMTokenClaims containing workspace_id, model settings, etc.

    Raises:
        ValueError: If token is invalid, expired, or missing required claims
    """
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    try:
        payload = jwt.decode(
            token,
            config.TRACECAT__SERVICE_KEY,
            algorithms=["HS256"],
            audience=LLM_TOKEN_AUDIENCE,
            issuer=LLM_TOKEN_ISSUER,
            options={"require": list(LLM_REQUIRED_CLAIMS)},
        )
    except PyJWTError as exc:
        raise ValueError(f"Invalid LLM token: {exc}") from exc

    if payload.get("sub") != LLM_TOKEN_SUBJECT:
        raise ValueError("Invalid LLM token subject")

    try:
        return LLMTokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid LLM token claims: {exc}") from exc
