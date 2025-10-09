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
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft.clouds import (
    AzureCloud,
    get_authorization_endpoint,
    get_management_scopes,
    get_token_endpoint,
    map_management_scopes,
)


def get_azure_setup_steps(service: str = "Azure Management") -> list[str]:
    """Get setup steps for Azure Management OAuth."""
    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Configure Azure Resource Manager delegated permissions",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID and cloud selection",
    ]


AC_DESCRIPTION = "OAuth provider for Azure Resource Manager delegated permissions"
AC_DEFAULT_SCOPES = get_management_scopes(AzureCloud.PUBLIC, delegated=True)
CC_DESCRIPTION = "OAuth provider for Azure Resource Manager application permissions"
CC_DEFAULT_SCOPES = get_management_scopes(AzureCloud.PUBLIC, delegated=False)


class AzureManagementOAuthConfig(BaseModel):
    """Configuration model for Azure Management OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. 'common' for multi-tenant apps, 'organizations' for work/school accounts, or a specific tenant GUID.",
        min_length=1,
        max_length=100,
    )
    cloud: AzureCloud = Field(
        default=AzureCloud.PUBLIC,
        description="Azure cloud environment. Use 'public' or 'us_gov'.",
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
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    config_model: ClassVar[type[BaseModel]] = AzureManagementOAuthConfig
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

    def __init__(
        self,
        tenant_id: str,
        cloud: AzureCloud = AzureCloud.PUBLIC,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Azure Management OAuth provider."""
        self.tenant_id = tenant_id
        self.cloud = AzureCloud(cloud)
        if kwargs.get("scopes") is None:
            kwargs["scopes"] = map_management_scopes(self.scopes.default, self.cloud)
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return get_authorization_endpoint(self.cloud, self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return get_token_endpoint(self.cloud, self.tenant_id)

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
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/azure/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes",
)


class AzureManagementCCProvider(ClientCredentialsOAuthProvider):
    """Azure Management OAuth provider using client credentials flow for application permissions."""

    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    config_model: ClassVar[type[BaseModel]] = AzureManagementOAuthConfig
    metadata: ClassVar[ProviderMetadata] = CC_METADATA

    def __init__(
        self,
        tenant_id: str,
        cloud: AzureCloud = AzureCloud.PUBLIC,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Azure Management client credentials provider."""
        self.tenant_id = tenant_id
        self.cloud = AzureCloud(cloud)
        if kwargs.get("scopes") is None:
            kwargs["scopes"] = map_management_scopes(self.scopes.default, self.cloud)
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return get_authorization_endpoint(self.cloud, self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return get_token_endpoint(self.cloud, self.tenant_id)
