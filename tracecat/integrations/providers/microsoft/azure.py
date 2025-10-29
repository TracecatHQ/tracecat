"""Azure Management OAuth integration for Azure Resource Manager APIs."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_COMMERCIAL_AUTHORIZATION_ENDPOINT as MS_DEFAULT_AUTH_ENDPOINT,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_COMMERCIAL_TOKEN_ENDPOINT as MS_DEFAULT_TOKEN_ENDPOINT,
)
from tracecat.integrations.providers.microsoft._common import (
    MICROSOFT_AUTH_ENDPOINT_HELP,
    MICROSOFT_TOKEN_ENDPOINT_HELP,
)
from tracecat.integrations.providers.microsoft._common import (
    get_ac_setup_steps as _common_get_ac_setup_steps,
)

DEFAULT_COMMERCIAL_AUTH_ENDPOINT = MS_DEFAULT_AUTH_ENDPOINT
DEFAULT_COMMERCIAL_TOKEN_ENDPOINT = MS_DEFAULT_TOKEN_ENDPOINT
AZURE_AUTH_ENDPOINT_HELP = MICROSOFT_AUTH_ENDPOINT_HELP
AZURE_TOKEN_ENDPOINT_HELP = MICROSOFT_TOKEN_ENDPOINT_HELP


def get_azure_setup_steps(service: str = "Azure Management") -> list[str]:
    """Get setup steps for Azure Management OAuth."""
    return _common_get_ac_setup_steps(service)


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
