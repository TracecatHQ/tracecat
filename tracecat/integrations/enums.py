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
    """Authentication type for MCP integrations.

    Supported types:
    - OAUTH2: OAuth 2.1 (standard for HTTP MCP servers per MCP spec)
    - CUSTOM: Custom authentication (for custom authentication)
    - NONE: No authentication (for no authentication)
    """

    OAUTH2 = "oauth2"
    CUSTOM = "custom"
    NONE = "none"
