"""Internal OIDC issuer configuration for MCP authentication."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tracecat.auth.secrets import get_user_auth_secret
from tracecat.config import TRACECAT__PUBLIC_API_URL

# --- Fixed internal client ---

INTERNAL_CLIENT_ID = "tracecat-mcp-oidc-internal"
"""The single confidential OAuth client ID used by the FastMCP proxy."""


def get_internal_client_secret() -> str:
    """Derive a deterministic client secret from USER_AUTH_SECRET.

    Uses HKDF with a unique context string so the secret is stable across
    restarts but isolated from other derived secrets.
    """
    secret_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"tracecat-mcp-oidc-client-secret-v1",
        info=b"client-secret",
    ).derive(get_user_auth_secret().encode("utf-8"))
    return base64.urlsafe_b64encode(secret_bytes).decode("ascii")


# --- Issuer URL ---

ISSUER_PATH_PREFIX = "/mcp-oidc"
"""Path prefix for the internal OIDC issuer within the API root."""


def get_issuer_url() -> str:
    """Return the public issuer URL for the internal OIDC issuer.

    This URL appears in discovery documents and JWT ``iss`` claims.
    It is built from the public API URL so browsers can reach it.
    """
    return f"{TRACECAT__PUBLIC_API_URL.rstrip('/')}{ISSUER_PATH_PREFIX}"


def get_internal_discovery_url() -> str:
    """Return the OIDC discovery URL reachable from the MCP server.

    Uses ``TRACECAT__API_URL`` for server-to-server communication
    (avoids hairpin NAT), falling back to the public API URL.
    """
    internal_api = (
        os.environ.get("TRACECAT__API_URL") or TRACECAT__PUBLIC_API_URL
    ).rstrip("/")
    return f"{internal_api}{ISSUER_PATH_PREFIX}/.well-known/openid-configuration"


# --- Lifetimes ---

ACCESS_TOKEN_LIFETIME_SECONDS = 24 * 60 * 60
"""Access token lifetime: 24 hours."""

AUTH_CODE_LIFETIME_SECONDS = 5 * 60
"""Authorization code lifetime: 5 minutes."""

RESUME_TRANSACTION_LIFETIME_SECONDS = 15 * 60
"""Resume/login transaction lifetime: 15 minutes."""

TOKEN_RATE_LIMIT_PER_SOURCE_PER_MINUTE = 120
"""Maximum token endpoint requests per source IP per minute.

Keyed by source IP because the token exchange is server-to-server
(MCP instance → API).  Each MCP instance has a distinct source IP,
so this gives per-instance rate limiting.  A single fixed client_id
would collapse to a global bucket if used as the key.
"""
