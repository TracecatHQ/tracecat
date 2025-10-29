"""Microsoft Graph OAuth integration using standardized endpoint configuration."""

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
GRAPH_AUTH_ENDPOINT_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize"
)
GRAPH_TOKEN_ENDPOINT_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/token"
)


def get_ac_setup_steps(service: str = "Microsoft Graph") -> list[str]:
    """Get setup steps for authorization code flow for a Microsoft service."""
    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Copy Client ID and Client Secret",
        (
            "Configure the authorization and token endpoints for your tenant "
            "(defaults use the commercial cloud with 'common')"
        ),
    ]


def get_cc_setup_steps(service: str = "Microsoft Graph") -> list[str]:
    """Get setup steps for client credentials flow for a Microsoft service."""
    return [
        "Register your application in Azure Portal",
        f"Configure API permissions for {service} with Application permissions (not Delegated)",
        "Grant admin consent for the application permissions",
        "Copy Client ID and Client Secret",
        (
            "Configure the authorization and token endpoints for your tenant "
            "(defaults use the commercial cloud with 'common')"
        ),
    ]


AC_DESCRIPTION = "OAuth provider for delegated user permissions"
CC_DESCRIPTION = "OAuth provider for application permissions (service account)"


# Shared Microsoft Graph scopes for authorization code flow
AC_SCOPES = ProviderScopes(
    default=["offline_access", "https://graph.microsoft.com/User.Read"],
)


# Shared metadata for authorization code flow
AC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph (Delegated)",
    description=f"Microsoft Graph {AC_DESCRIPTION}",
    setup_steps=get_ac_setup_steps(),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-user",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Graph OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = GRAPH_AUTH_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = GRAPH_TOKEN_ENDPOINT_HELP

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Microsoft Graph-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }


# Shared Microsoft Graph scopes for client credentials flow
CC_SCOPES = ProviderScopes(
    # Client credentials flow requires .default scope.
    # App permissions are configured in Azure Portal.
    default=["https://graph.microsoft.com/.default"],
)

# Shared metadata for client credentials flow
CC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph (Service Principal)",
    description=f"Microsoft Graph {CC_DESCRIPTION}",
    setup_steps=get_cc_setup_steps(),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Graph OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = GRAPH_AUTH_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = GRAPH_TOKEN_ENDPOINT_HELP
