"""Azure Management OAuth integration for Azure Resource Manager APIs."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_AUTHORIZATION_ENDPOINT,
    DEFAULT_TOKEN_ENDPOINT,
    get_ac_setup_steps,
    get_cc_setup_steps,
)

API_DOCS_URL = "https://learn.microsoft.com/en-us/rest/api/azure/"
AC_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app"
CC_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow"
TROUBLESHOOTING_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes"

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
    setup_steps=get_ac_setup_steps(service="Azure Management"),
    requires_config=True,
    enabled=True,
    api_docs_url=API_DOCS_URL,
    setup_guide_url=AC_SETUP_GUIDE_URL,
    troubleshooting_url=TROUBLESHOOTING_URL,
)

CC_SCOPES = ProviderScopes(
    default=CC_DEFAULT_SCOPES,
)
CC_METADATA = ProviderMetadata(
    id="azure_management",
    name="Azure Management (Service Principal)",
    description=f"Azure Management {CC_DESCRIPTION}",
    setup_steps=get_cc_setup_steps(service="Azure Management"),
    requires_config=True,
    enabled=True,
    api_docs_url=API_DOCS_URL,
    setup_guide_url=CC_SETUP_GUIDE_URL,
    troubleshooting_url=TROUBLESHOOTING_URL,
)


class AzureManagementACProvider(AuthorizationCodeOAuthProvider):
    """Azure Management OAuth provider using authorization code flow for delegated user permissions."""

    default_tenant: ClassVar[str] = "organizations"
    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT


class AzureManagementCCProvider(ClientCredentialsOAuthProvider):
    """Azure Management OAuth provider using client credentials flow for application permissions."""

    default_tenant: ClassVar[str] = "organizations"
    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT
