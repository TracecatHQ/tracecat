"""Microsoft OAuth integration using MSAL (Microsoft Authentication Library)."""

import os

from dotenv import load_dotenv
from msal import ConfidentialClientApplication

from tracecat import config
from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.models import TokenResponse
from tracecat.logger import logger

load_dotenv()


class MicrosoftOAuthProvider(BaseOauthProvider):
    """Microsoft OAuth provider using MSAL."""

    id = "microsoft"

    def __init__(self):
        """Initialize the Microsoft OAuth provider with MSAL."""
        self.client_id = os.getenv("MICROSOFT_CLIENT_ID")
        self.client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID")

        # Microsoft Graph scopes for Teams integration
        self.scopes = [
            "https://graph.microsoft.com/Team.ReadBasic.All",
            "https://graph.microsoft.com/Channel.ReadBasic.All",
            "https://graph.microsoft.com/ChannelMessage.Send",
            "https://graph.microsoft.com/User.Read",
        ]

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Microsoft OAuth credentials not configured. "
                "Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET environment variables."
            )

        # Create MSAL confidential client application
        authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.app = ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=authority,
        )

        self.logger = logger.bind(service="MicrosoftOAuthProvider")
        self.logger.warning(
            "Microsoft OAuth credentials not configured. ",
            public_api_url=config.TRACECAT__PUBLIC_API_URL,
            redirect_uri=self.redirect_uri,
            client_id=self.client_id,
            tenant_id=self.tenant_id,
        )

    async def get_authorization_url(self, state: str) -> str:
        """Get the Microsoft OAuth authorization URL."""
        auth_url = self.app.get_authorization_request_url(
            scopes=self.scopes,
            redirect_uri=self.redirect_uri,
            state=state,
        )

        self.logger.info("Generated Microsoft OAuth authorization URL", state=state)
        return auth_url

    async def exchange_code_for_token(self, code: str, state: str) -> TokenResponse:
        """Exchange authorization code for access token."""
        try:
            result = self.app.acquire_token_by_authorization_code(
                code=code,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri,
            )

            if "error" in result:
                self.logger.error(
                    "Failed to acquire token",
                    error=result.get("error"),
                    error_description=result.get("error_description"),
                )
                raise ValueError(
                    f"Token acquisition failed: {result.get('error_description')}"
                )

            self.logger.info(
                "Successfully acquired Microsoft OAuth token",
                state=state,
            )

            return TokenResponse(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token"),
                expires_in=result.get("expires_in", 3600),
                scope=" ".join(result.get("scope", [])),
                token_type=result.get("token_type", "Bearer"),
            )

        except Exception as e:
            self.logger.error(
                "Error exchanging code for token",
                error=str(e),
                state=state,
            )
            raise
