"""Microsoft OAuth integration using generic OAuth provider."""

from typing import Any, ClassVar, Unpack

from pydantic import BaseModel, Field

from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.models import (
    OAuthProviderKwargs,
    ProviderCategory,
    ProviderMetadata,
)


class MicrosoftOAuthConfig(BaseModel):
    """Configuration model for Microsoft OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. Use 'common' for multi-tenant apps, 'organizations' for work/school accounts, 'consumers' for personal accounts, or a specific tenant GUID",
        min_length=1,
        max_length=100,
    )


class MicrosoftOAuthProvider(BaseOAuthProvider):
    """Microsoft OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "microsoft"

    # Microsoft OAuth endpoints
    _authorization_endpoint: ClassVar[str] = (
        "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    )
    _token_endpoint: ClassVar[str] = (
        "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    )

    # Default Microsoft Graph scopes for Teams integration
    default_scopes: ClassVar[list[str]] = [
        "offline_access",  # Required for refresh token
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/Team.ReadBasic.All",
        "https://graph.microsoft.com/Channel.ReadBasic.All",
        "https://graph.microsoft.com/ChannelMessage.Send",
    ]

    config_model: ClassVar[type[BaseModel]] = MicrosoftOAuthConfig

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft",
        name="Microsoft",
        description="Microsoft OAuth provider",
        categories=[ProviderCategory.AUTH],
        features=[
            "OAuth 2.0",
            "Azure AD Integration",
            "Microsoft Graph API",
            "Single Sign-On",
        ],
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

    def __init__(
        self,
        tenant_id: str,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft OAuth provider."""
        # Get tenant ID for Microsoft
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
        """Add Microsoft-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }
