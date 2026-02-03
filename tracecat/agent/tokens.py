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

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, Field, ValidationError

from tracecat import config
from tracecat.agent.types import OutputType
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID

# -----------------------------------------------------------------------------
# MCP Token (for tool execution)
# -----------------------------------------------------------------------------

MCP_TOKEN_ISSUER = "tracecat-agent-executor"
MCP_TOKEN_AUDIENCE = "tracecat-mcp-server"
MCP_TOKEN_SUBJECT = "tracecat-agent-runtime"
MCP_REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "iat",
    "exp",
    "workspace_id",
    "organization_id",
    "allowed_actions",
    "session_id",
)


class UserMCPServerClaim(BaseModel):
    """User MCP server configuration in JWT claims."""

    name: str
    """Unique identifier for the server."""
    url: str
    """HTTP/SSE endpoint URL."""
    transport: Literal["http", "sse"] = "http"
    """Transport type: 'http' or 'sse'."""
    headers: dict[str, str] = Field(default_factory=dict)
    """Auth headers."""


class InternalToolContext(BaseModel):
    """Context for internal tools (not in registry).

    Internal tools are system-level tools that have direct database access
    but are not part of the registry (not usable in workflows).
    """

    preset_id: uuid.UUID | None = None
    """Agent preset ID for builder tools."""
    entity_type: str | None = None
    """Entity type (e.g., 'agent_preset_builder') to determine which internal tools to expose."""


class MCPTokenClaims(BaseModel):
    """Claims extracted from a verified MCP token.

    Contains minimal identity info needed for action execution.
    The Role is reconstructed on the trusted side in the executor.
    """

    workspace_id: WorkspaceID
    """Workspace UUID for authorization context."""
    user_id: UserID | None = None
    """Optional user ID for audit/traceability."""
    session_id: uuid.UUID
    """Agent session ID for traceability."""
    organization_id: OrganizationID
    """Organization UUID for authorization context."""
    allowed_actions: list[str]
    """Set of allowed action names (e.g., {"tools.slack.post_message", "core.http_request"})."""
    user_mcp_servers: list[UserMCPServerClaim] = Field(default_factory=list)
    """User-defined MCP server configurations for proxying tool calls."""
    allowed_internal_tools: list[str] = Field(default_factory=list)
    """Set of allowed internal tool names (e.g., {"internal.builder.get_preset_summary"})."""
    internal_tool_context: InternalToolContext | None = None
    """Context for internal tools (preset_id, entity_type, etc.)."""


def mint_mcp_token(
    *,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    allowed_actions: list[str],
    session_id: uuid.UUID,
    user_id: UserID | None = None,
    user_mcp_servers: list[UserMCPServerClaim] | None = None,
    allowed_internal_tools: list[str] | None = None,
    internal_tool_context: InternalToolContext | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed MCP JWT containing workspace identity and allowed actions.

    This token is minted by the AgentExecutor (trusted) and passed to the
    jailed runtime. The runtime cannot decode or modify it - it's opaque.

    The token contains minimal identity info (workspace_id, organization_id, user_id)
    rather than a full Role object. The Role is reconstructed on the trusted side
    when the token is verified, providing better security isolation.

    Args:
        workspace_id: Workspace UUID for authorization context
        organization_id: Organization UUID for authorization context
        allowed_actions: Set of allowed action names
        session_id: Agent session ID for traceability
        user_id: Optional user ID for audit/traceability
        user_mcp_servers: User-defined MCP server configs for proxying
        allowed_internal_tools: Set of allowed internal tool names
        internal_tool_context: Context for internal tools (preset_id, entity_type)
        ttl_seconds: Token TTL in seconds (defaults to executor token TTL)

    Returns:
        Signed JWT string
    """
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    now = datetime.now(UTC)
    ttl = ttl_seconds or config.TRACECAT__EXECUTOR_TOKEN_TTL_SECONDS

    payload: dict[str, Any] = {
        "iss": MCP_TOKEN_ISSUER,
        "aud": MCP_TOKEN_AUDIENCE,
        "sub": MCP_TOKEN_SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "workspace_id": str(workspace_id),
        "organization_id": str(organization_id),
        "allowed_actions": allowed_actions,
        "session_id": str(session_id),
    }

    if user_id is not None:
        payload["user_id"] = str(user_id)

    if user_mcp_servers:
        payload["user_mcp_servers"] = [s.model_dump() for s in user_mcp_servers]

    if allowed_internal_tools:
        payload["allowed_internal_tools"] = allowed_internal_tools

    if internal_tool_context:
        payload["internal_tool_context"] = internal_tool_context.model_dump(mode="json")

    return jwt.encode(payload, config.TRACECAT__SERVICE_KEY, algorithm="HS256")


def verify_mcp_token(token: str) -> MCPTokenClaims:
    """Verify MCP JWT and return extracted claims.

    Args:
        token: The JWT string to verify

    Returns:
        MCPTokenClaims containing workspace identity and allowed_actions

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

    try:
        return MCPTokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("Invalid MCP token claims") from exc


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
    "organization_id",
    "session_id",
    "model",
    "provider",
)


class LLMTokenClaims(BaseModel):
    """Claims extracted from a verified LLM token.

    These claims are set by the AgentExecutor when minting the token
    and are immutable - the jailed runtime cannot modify them.
    """

    # Identity
    workspace_id: WorkspaceID = Field(..., description="Workspace UUID")
    organization_id: OrganizationID = Field(..., description="Organization UUID")
    session_id: uuid.UUID = Field(..., description="Agent session UUID")

    # Model configuration
    model: str = Field(..., description="The model to use for this run")
    provider: str = Field(
        ..., description="The provider for the model (e.g., openai, anthropic, bedrock)"
    )

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
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    session_id: uuid.UUID,
    model: str,
    provider: str,
    model_settings: dict[str, Any] | None = None,
    output_type: OutputType | None = None,
    use_workspace_credentials: bool = False,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed LLM JWT for jailed agent runtime.

    This token is minted by the AgentExecutor (trusted) and passed to the
    jailed runtime. The runtime cannot decode or modify it - it's opaque.

    Args:
        workspace_id: The workspace UUID
        organization_id: The organization UUID
        session_id: The agent session UUID
        model: The model to use for this run
        provider: The provider for the model (e.g., openai, anthropic, bedrock)
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
        "workspace_id": str(workspace_id),
        "organization_id": str(organization_id),
        "session_id": str(session_id),
        # Model configuration
        "model": model,
        "provider": provider,
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
        raise ValueError("Invalid LLM token") from exc

    if payload.get("sub") != LLM_TOKEN_SUBJECT:
        raise ValueError("Invalid LLM token subject")

    try:
        return LLMTokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("Invalid LLM token claims") from exc
