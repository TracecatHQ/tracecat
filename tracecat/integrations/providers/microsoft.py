"""Microsoft OAuth integration using generic OAuth provider."""

from typing import Any, ClassVar

from dotenv import load_dotenv
from pydantic import BaseModel

from tracecat.integrations.base import BaseOauthProvider

load_dotenv()


class MicrosoftOAuthConfig(BaseModel):
    tenant_id: str


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

    def __init__(self, tenant_id: str):
        """Initialize the Microsoft OAuth provider."""
        # Get tenant ID for Microsoft
        self.tenant_id = tenant_id

        # Initialize parent class
        super().__init__()

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
