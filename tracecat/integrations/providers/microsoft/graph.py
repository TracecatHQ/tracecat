"""Microsoft Graph OAuth integration using generic OAuth provider."""

from typing import Any, ClassVar, Unpack

from pydantic import BaseModel, Field

from tracecat.integrations.models import (
    OAuthProviderKwargs,
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)

# Shared Microsoft Graph OAuth constants
AUTHORIZATION_ENDPOINT = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
)
TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class MicrosoftGraphOAuthConfig(BaseModel):
    """Configuration model for Microsoft Graph OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. 'common' for multi-tenant apps, 'organizations' for work/school accounts, 'consumers' for personal accounts, or a specific tenant GUID.",
        min_length=1,
        max_length=100,
    )


# Shared Microsoft Graph scopes for authorization code flow
AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://graph.microsoft.com/User.Read",
    ],
    allowed_patterns=[
        r"^https://graph\.microsoft\.com/[^/]+$",
        r"^(?!.*\.ReadWrite\.All$).*",  # Prevent read/write all patterns
        r"^(?!.*\.Read\.All$).*",  # Prevent read all patterns
        r"^(?!.*\.Write\.All$).*",  # Prevent write all patterns
    ],
)


# Shared metadata for authorization code flow
AC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph",
    description="Microsoft Graph OAuth provider for delegated user permissions",
    categories=[ProviderCategory.AUTH],
    setup_steps=[
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        "Configure required API permissions for Microsoft Graph",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID",
    ],
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/overview?view=graph-rest-1.0",
    setup_guide_url="https://developer.microsoft.com/en-us/graph/quick-start",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Graph OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_graph"

    # Use shared constants
    _authorization_endpoint: ClassVar[str] = AUTHORIZATION_ENDPOINT
    _token_endpoint: ClassVar[str] = TOKEN_ENDPOINT
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    config_model: ClassVar[type[BaseModel]] = MicrosoftGraphOAuthConfig
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

    def __init__(
        self,
        tenant_id: str,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft Graph OAuth provider."""
        # Get tenant ID for Microsoft Graph
        self.tenant_id = tenant_id

        # Initialize parent class with credentials
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint.format(tenant=self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint.format(tenant=self.tenant_id)

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Microsoft Graph-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }


# Shared Microsoft Graph scopes for client credentials flow
CC_SCOPES = ProviderScopes(
    # For Microsoft Entra ID / Graph client-credentials tokens you cannot ask
    # for individual Microsoft Graph scopes (e.g. Chat.Read, Channel.Read.All, …)
    # in the scope parameter. The token endpoint for app-only auth accepts one
    # and only one value per resource and that value must be the ".default" scope:
    # If you need additional permissions (Chat.Read, ChannelMessage.Send, …)
    # you add them to API permissions → Application permissions in the portal
    # and click Grant admin consent. After that a token requested with .default
    # automatically contains those new roles.
    default=[
        "https://graph.microsoft.com/.default",
    ],
    accepts_additional_scopes=False,
)

# Shared metadata for client credentials flow
CC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph",
    description="Microsoft Graph OAuth provider for application permissions (service account)",
    categories=[ProviderCategory.AUTH],
    setup_steps=[
        "Register your application in Azure Portal",
        "Configure API permissions for Microsoft Graph with Application permissions (not Delegated)",
        "Grant admin consent for the application permissions",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID",
        "Use scopes like 'https://graph.microsoft.com/.default' for client credentials flow",
    ],
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Graph OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_graph"

    # Use shared constants
    _authorization_endpoint: ClassVar[str] = AUTHORIZATION_ENDPOINT
    _token_endpoint: ClassVar[str] = TOKEN_ENDPOINT
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    config_model: ClassVar[type[BaseModel]] = MicrosoftGraphOAuthConfig
    metadata: ClassVar[ProviderMetadata] = CC_METADATA

    def __init__(
        self,
        tenant_id: str,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft Graph client credentials OAuth provider."""
        # Store tenant ID for Microsoft Graph
        self.tenant_id = tenant_id

        # Initialize parent class with credentials
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint.format(tenant=self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint.format(tenant=self.tenant_id)
