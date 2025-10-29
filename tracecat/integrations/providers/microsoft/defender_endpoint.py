"""Microsoft Defender for Endpoint OAuth integration."""

from __future__ import annotations

from typing import Any, ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)

DEFAULT_COMMERCIAL_AUTH_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
)
DEFAULT_COMMERCIAL_TOKEN_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)
DEFENDER_AUTH_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize"
)
DEFENDER_TOKEN_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/token"
)

SETUP_STEPS = [
    "Register a Microsoft Entra application with access to Microsoft Defender for Endpoint",
    "Add the redirect URI shown above to Redirect URIs",
    "Configure the required Defender for Endpoint delegated or application permissions and grant admin consent",
    "Copy the client ID and client secret",
    "Configure the authorization and token endpoints for your tenant (defaults use the commercial cloud with 'common')",
    "Update scopes if you are using sovereign cloud resource URIs (e.g. GCC High)",
]


AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://api.securitycenter.microsoft.com/.default",
    ],
)

AC_METADATA = ProviderMetadata(
    id="microsoft_defender_endpoint",
    name="Microsoft Defender for Endpoint (Delegated)",
    description="Microsoft Defender for Endpoint delegated authentication for investigation and response APIs.",
    setup_steps=SETUP_STEPS,
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
    setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/get-started",
    troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
)


class MicrosoftDefenderEndpointACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider for delegated user permissions."""

    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = DEFENDER_AUTH_HELP
    token_endpoint_help: ClassVar[str | None] = DEFENDER_TOKEN_HELP

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Microsoft-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }


CC_SCOPES = ProviderScopes(
    default=["https://api.securitycenter.microsoft.com/.default"],
)

CC_METADATA = ProviderMetadata(
    id="microsoft_defender_endpoint",
    name="Microsoft Defender for Endpoint (Service Principal)",
    description="Microsoft Defender for Endpoint service principal authentication for automated investigation and response.",
    setup_steps=SETUP_STEPS,
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
    setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/get-started",
    troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
)


class MicrosoftDefenderEndpointCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider using client credentials flow."""

    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = DEFENDER_AUTH_HELP
    token_endpoint_help: ClassVar[str | None] = DEFENDER_TOKEN_HELP
