"""Microsoft Graph OAuth integration using generic OAuth provider."""

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
    get_graph_scopes,
    get_token_endpoint,
    map_graph_scopes,
)


def get_ac_setup_steps(service: str = "Microsoft Graph") -> list[str]:
    """Get setup steps for authorization code flow for a Microsoft service."""
    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID and cloud selection",
    ]


def get_cc_setup_steps(service: str = "Microsoft Graph") -> list[str]:
    """Get setup steps for client credentials flow for a Microsoft service."""
    return [
        "Register your application in Azure Portal",
        f"Configure API permissions for {service} with Application permissions (not Delegated)",
        "Grant admin consent for the application permissions",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID and cloud selection",
        "Use scopes like 'https://graph.microsoft.com/.default' (adjust domain per cloud)",
    ]


AC_DESCRIPTION = "OAuth provider for delegated user permissions"
CC_DESCRIPTION = "OAuth provider for application permissions (service account)"


class MicrosoftGraphOAuthConfig(BaseModel):
    """Configuration model for Microsoft Graph OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. 'common' for multi-tenant apps, 'organizations' for work/school accounts, 'consumers' for personal accounts, or a specific tenant GUID.",
        min_length=1,
        max_length=100,
    )
    cloud: AzureCloud = Field(
        default=AzureCloud.PUBLIC,
        description="Azure cloud environment. Use 'public' or 'us_gov'.",
    )


# Shared Microsoft Graph scopes for authorization code flow
AC_SCOPES = ProviderScopes(
    default=get_graph_scopes(AzureCloud.PUBLIC, delegated=True),
)


# Shared metadata for authorization code flow
AC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph (Delegated)",
    description=f"Microsoft Graph {AC_DESCRIPTION}",
    setup_steps=get_ac_setup_steps(),
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-user",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Graph OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    config_model: ClassVar[type[BaseModel]] = MicrosoftGraphOAuthConfig
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

    def __init__(
        self,
        tenant_id: str,
        cloud: AzureCloud = AzureCloud.PUBLIC,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft Graph OAuth provider."""
        self.tenant_id = tenant_id
        self.cloud = AzureCloud(cloud)
        if kwargs.get("scopes") is None:
            kwargs["scopes"] = map_graph_scopes(self.scopes.default, self.cloud)
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return get_authorization_endpoint(self.cloud, self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return get_token_endpoint(self.cloud, self.tenant_id)

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
    default=get_graph_scopes(AzureCloud.PUBLIC, delegated=False),
)

# Shared metadata for client credentials flow
CC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph (Service account)",
    description=f"Microsoft Graph {CC_DESCRIPTION}",
    setup_steps=get_cc_setup_steps(),
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Graph OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    config_model: ClassVar[type[BaseModel]] = MicrosoftGraphOAuthConfig
    metadata: ClassVar[ProviderMetadata] = CC_METADATA

    def __init__(
        self,
        tenant_id: str,
        cloud: AzureCloud = AzureCloud.PUBLIC,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft Graph client credentials OAuth provider."""
        self.tenant_id = tenant_id
        self.cloud = AzureCloud(cloud)
        if kwargs.get("scopes") is None:
            kwargs["scopes"] = map_graph_scopes(self.scopes.default, self.cloud)
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return get_authorization_endpoint(self.cloud, self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return get_token_endpoint(self.cloud, self.tenant_id)
