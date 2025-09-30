"""Azure Management OAuth integration for Azure Resource Manager APIs."""

from typing import Any, ClassVar, Unpack

from pydantic import BaseModel, Field

from tracecat.integrations.models import (
    OAuthProviderKwargs,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
)

AUTHORIZATION_ENDPOINT = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
)
TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


def get_azure_setup_steps(service: str = "Azure Management") -> list[str]:
    """Get setup steps for Azure Management OAuth."""
    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Configure Azure Resource Manager delegated permissions",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID",
    ]


AC_DESCRIPTION = "OAuth provider for Azure Resource Manager delegated permissions"
AC_DEFAULT_SCOPES = [
    "offline_access",
    "https://management.azure.com/user_impersonation",
]


class AzureManagementOAuthConfig(BaseModel):
    """Configuration model for Azure Management OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. 'common' for multi-tenant apps, 'organizations' for work/school accounts, or a specific tenant GUID.",
        min_length=1,
        max_length=100,
    )


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
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/azure/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes",
)


class AzureManagementACProvider(AuthorizationCodeOAuthProvider):
    """Azure Management OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "azure_management"
    _authorization_endpoint: ClassVar[str] = AUTHORIZATION_ENDPOINT
    _token_endpoint: ClassVar[str] = TOKEN_ENDPOINT
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    config_model: ClassVar[type[BaseModel]] = AzureManagementOAuthConfig
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

    def __init__(
        self,
        tenant_id: str,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Azure Management OAuth provider."""
        self.tenant_id = tenant_id
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint.format(tenant=self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint.format(tenant=self.tenant_id)

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Azure-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }
