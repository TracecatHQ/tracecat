"""Shared helpers and base classes for Microsoft OAuth providers."""

import re
from typing import Any, ClassVar

from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)

MICROSOFT_DEFAULT_AUTHORIZATION_ENDPOINT = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
)
MICROSOFT_DEFAULT_TOKEN_ENDPOINT = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
)

MICROSOFT_SOVEREIGN_AUTH_HELP: list[str] = [
    "Cloud-specific authorize endpoints:",
    "- Commercial (Public): https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
    "- US Gov (GCC/GCC High/DoD): https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize",
    "\n",
    "Replace {tenant} in the authorization and token endpoints with your directory (tenant) ID, 'common' for multi-tenant applications, or 'consumers' for personal accounts.",
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


GUID_PATTERN = re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")


def _strip_guid_dashes(value: str | None) -> str | None:
    """Return GUIDs without dashes; leave other inputs untouched."""
    if value is None:
        return None

    trimmed = value.strip()
    return trimmed.replace("-", "") if GUID_PATTERN.fullmatch(trimmed) else trimmed


def _sanitize_microsoft_endpoint(endpoint: str | None) -> str | None:
    """Remove dashes from GUID-like substrings within a URL."""
    if not endpoint:
        return endpoint

    return GUID_PATTERN.sub(lambda m: m.group(0).replace("-", ""), endpoint)


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

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        scopes: list[str] | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(
            client_id=_strip_guid_dashes(client_id),
            client_secret=client_secret,
            scopes=scopes,
            authorization_endpoint=_sanitize_microsoft_endpoint(authorization_endpoint),
            token_endpoint=_sanitize_microsoft_endpoint(token_endpoint),
            **kwargs,
        )


class MicrosoftClientCredentialsOAuthProvider(ClientCredentialsOAuthProvider):
    """Base class for Microsoft client-credentials OAuth providers."""

    default_authorization_endpoint: ClassVar[str] = (
        MICROSOFT_DEFAULT_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str] = MICROSOFT_DEFAULT_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[list[str]] = MICROSOFT_SOVEREIGN_AUTH_HELP
    token_endpoint_help: ClassVar[list[str]] = MICROSOFT_SOVEREIGN_TOKEN_HELP

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        scopes: list[str] | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(
            client_id=_strip_guid_dashes(client_id),
            client_secret=client_secret,
            scopes=scopes,
            authorization_endpoint=_sanitize_microsoft_endpoint(authorization_endpoint),
            token_endpoint=_sanitize_microsoft_endpoint(token_endpoint),
            **kwargs,
        )
