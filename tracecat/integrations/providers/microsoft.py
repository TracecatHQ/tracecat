"""Microsoft OAuth integration using generic OAuth provider."""

import os
from typing import Any, ClassVar

from dotenv import load_dotenv

from tracecat.integrations.base import BaseOauthProvider

load_dotenv()


class MicrosoftOAuthProvider(BaseOauthProvider):
    """Microsoft OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "microsoft"

    # Microsoft OAuth endpoints
    authorization_endpoint: ClassVar[str] = (
        "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    )
    token_endpoint: ClassVar[str] = (
        "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    )

    # Default Microsoft Graph scopes for Teams integration
    default_scopes: ClassVar[list[str]] = [
        "https://graph.microsoft.com/Team.ReadBasic.All",
        "https://graph.microsoft.com/Channel.ReadBasic.All",
        "https://graph.microsoft.com/ChannelMessage.Send",
        "https://graph.microsoft.com/User.Read",
    ]

    def __init__(self):
        """Initialize the Microsoft OAuth provider."""
        # Get tenant ID for Microsoft
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")

        # Update endpoints with tenant
        self.__class__.authorization_endpoint = self.authorization_endpoint.format(
            tenant=self.tenant_id
        )
        self.__class__.token_endpoint = self.token_endpoint.format(
            tenant=self.tenant_id
        )

        # Initialize parent class
        super().__init__()

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Microsoft-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }
