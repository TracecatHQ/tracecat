"""Shared helpers and base classes for Microsoft OAuth providers."""

from typing import ClassVar

from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)

MICROSOFT_DEFAULT_AUTHORIZATION_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
)
MICROSOFT_DEFAULT_TOKEN_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)

MICROSOFT_SOVEREIGN_AUTH_HELP: list[str] = [
    "Cloud-specific authorize endpoints:",
    "- Commercial (Public): https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
    "- US Gov (GCC/GCC High/DoD): https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize",
]

MICROSOFT_SOVEREIGN_TOKEN_HELP: list[str] = [
    "Cloud-specific token endpoints:",
    "- Commercial (Public): https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
    "- US Gov (GCC/GCC High/DoD): https://login.microsoftonline.us/{tenant}/oauth2/v2.0/token",
]

MICROSOFT_SETUP_STEPS: list[str] = [
    "Create an Entra ID application in Azure Portal",
    "Add the redirect URI shown above to 'Redirect URIs'",
    "Configure required API permissions and scopes in the application registration",
    "Copy the application ID and client secret",
    "Configure the authorization and token endpoints for your tenant in Tracecat",
]


def get_ac_description(service: str = "Microsoft Graph") -> str:
    """Get description for authorization code flow for a Microsoft service."""
    return f"{service} OAuth provider for delegated permissions"


def get_cc_description(service: str = "Microsoft Graph") -> str:
    """Get description for client credentials flow for a Microsoft service."""
    return f"{service} OAuth provider for application permissions (service principal)"


class MicrosoftAuthorizationCodeOAuthProvider(AuthorizationCodeOAuthProvider):
    """Base class for Microsoft authorization-code OAuth providers."""

    default_authorization_endpoint: ClassVar[str] = (
        MICROSOFT_DEFAULT_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str] = MICROSOFT_DEFAULT_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[list[str]] = MICROSOFT_SOVEREIGN_AUTH_HELP
    token_endpoint_help: ClassVar[list[str]] = MICROSOFT_SOVEREIGN_TOKEN_HELP


class MicrosoftClientCredentialsOAuthProvider(ClientCredentialsOAuthProvider):
    """Base class for Microsoft client-credentials OAuth providers."""

    default_authorization_endpoint: ClassVar[str] = (
        MICROSOFT_DEFAULT_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str] = MICROSOFT_DEFAULT_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[list[str]] = MICROSOFT_SOVEREIGN_AUTH_HELP
    token_endpoint_help: ClassVar[list[str]] = MICROSOFT_SOVEREIGN_TOKEN_HELP
