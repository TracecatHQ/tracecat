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
    """Type of OAuth flow."""

    AUTHORIZATION_CODE = "authorization_code"
    CLIENT_CREDENTIALS = "client_credentials"
