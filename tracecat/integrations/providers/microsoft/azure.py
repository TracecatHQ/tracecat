"""Azure Management OAuth integration for Azure Resource Manager APIs."""

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
AZURE_AUTH_ENDPOINT_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize"
)
AZURE_TOKEN_ENDPOINT_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/token"
)


def get_azure_setup_steps(service: str = "Azure Management") -> list[str]:
    """Get setup steps for Azure Management OAuth."""
    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Copy Client ID and Client Secret",
        "Configure the authorization and token endpoints for your tenant (defaults use the commercial cloud with 'common')",
    ]


AC_DESCRIPTION = "OAuth provider for Azure Resource Manager delegated permissions"
AC_DEFAULT_SCOPES = [
    "offline_access",
    "https://management.azure.com/user_impersonation",
]
CC_DESCRIPTION = "OAuth provider for Azure Resource Manager application permissions"
CC_DEFAULT_SCOPES = ["https://management.azure.com/.default"]


# Shared Azure Management scopes for authorization code flow
AC_SCOPES = ProviderScopes(
    default=AC_DEFAULT_SCOPES,
)


# Shared metadata for authorization code flow
AC_METADATA = ProviderMetadata(
    id="azure_management",
    name="Azure Management (Delegated)",
    description=f"Azure Management {AC_DESCRIPTION}",
    setup_steps=get_azure_setup_steps(),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/azure/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes",
)


class AzureManagementACProvider(AuthorizationCodeOAuthProvider):
    """Azure Management OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = AZURE_AUTH_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = AZURE_TOKEN_ENDPOINT_HELP

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Azure-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }


CC_SCOPES = ProviderScopes(
    default=CC_DEFAULT_SCOPES,
)


CC_METADATA = ProviderMetadata(
    id="azure_management",
    name="Azure Management (Service Principal)",
    description=f"Azure Management {CC_DESCRIPTION}",
    setup_steps=get_azure_setup_steps(),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/azure/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes",
)


class AzureManagementCCProvider(ClientCredentialsOAuthProvider):
    """Azure Management OAuth provider using client credentials flow for application permissions."""

    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = AZURE_AUTH_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = AZURE_TOKEN_ENDPOINT_HELP
