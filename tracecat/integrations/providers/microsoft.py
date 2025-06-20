"""Microsoft OAuth integration using generic OAuth provider."""

from typing import Any, ClassVar, Unpack

from pydantic import BaseModel, Field

from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.models import OAuthProviderKwargs


class MicrosoftOAuthConfig(BaseModel):
    """Configuration model for Microsoft OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. Use 'common' for multi-tenant apps, 'organizations' for work/school accounts, 'consumers' for personal accounts, or a specific tenant GUID",
        min_length=1,
        max_length=100,
    )


class MicrosoftOAuthProvider(BaseOauthProvider):
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
        "https://graph.microsoft.com/Team.ReadBasic.All",
        "https://graph.microsoft.com/Channel.ReadBasic.All",
        "https://graph.microsoft.com/ChannelMessage.Send",
        "https://graph.microsoft.com/User.Read",
    ]

    config_model: ClassVar[type[BaseModel]] = MicrosoftOAuthConfig

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
