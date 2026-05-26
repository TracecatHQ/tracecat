from enum import StrEnum


class IntegrationStatus(StrEnum):
    """Status of an integration."""

    NOT_CONFIGURED = "not_configured"
    """The integration is not configured."""
    CONFIGURED = "configured"
    """The integration is configured but not connected."""
    CONNECTED = "connected"
    """The integration is connected."""


class OAuthGrantType(StrEnum):
    """Grant type for OAuth 2.0."""

    AUTHORIZATION_CODE = "authorization_code"
    """Authorization code grant type. See https://datatracker.ietf.org/doc/html/rfc6749#section-1.3.1"""
    CLIENT_CREDENTIALS = "client_credentials"
    """Client credentials grant type. See https://datatracker.ietf.org/doc/html/rfc6749#section-1.3.4"""


class MCPAuthType(StrEnum):
    """Authentication type for MCP integrations."""

    OAUTH2 = "OAUTH2"
    CUSTOM = "CUSTOM"
    NONE = "NONE"


class IntegrationSource(StrEnum):
    """Origin of a catalog integration."""

    PLATFORM = "platform"
    """Tracecat-shipped, available across workspaces."""
    WORKSPACE = "workspace"
    """Workspace-authored catalog entry."""


class ConnectionAuthMethod(StrEnum):
    """Auth method for catalog connection projections."""

    OAUTH_AUTH_CODE = "oauth_auth_code"
    """OAuth 2.0 Authorization Code grant (user-delegated, per-user)."""
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"
    """OAuth 2.0 Client Credentials grant (machine-to-machine)."""
    SERVICE_ACCOUNT = "service_account"
    """Service account JSON (e.g. GCP)."""
    STATIC_KV = "static_kv"
    """Generic encrypted key-value blob."""
