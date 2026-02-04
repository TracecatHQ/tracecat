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
    JWT_BEARER = "jwt_bearer"
    """JWT bearer grant type. See https://datatracker.ietf.org/doc/html/rfc7523"""


class MCPAuthType(StrEnum):
    """Authentication type for MCP integrations."""

    OAUTH2 = "OAUTH2"
    CUSTOM = "CUSTOM"
    NONE = "NONE"
