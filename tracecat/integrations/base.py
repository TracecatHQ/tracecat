"""Base OAuth provider using authlib for standardized OAuth2 flows."""

import os
from typing import Any, ClassVar, cast

from authlib.integrations.httpx_client import AsyncOAuth2Client
from dotenv import load_dotenv

from tracecat import config
from tracecat.integrations.models import TokenResponse
from tracecat.logger import logger

load_dotenv()


class BaseOauthProvider:
    """Base OAuth provider using authlib for standardized OAuth2 flows."""

    id: ClassVar[str]

    # OAuth2 endpoints - to be overridden by subclasses
    authorization_endpoint: ClassVar[str | None] = None
    token_endpoint: ClassVar[str | None] = None

    # Default scopes - to be overridden by subclasses
    default_scopes: ClassVar[list[str]] = []

    # OAuth2 configuration
    response_type: ClassVar[str] = "code"
    grant_type: ClassVar[str] = "authorization_code"

    @property
    def base_url(self) -> str:
        return f"{config.TRACECAT__PUBLIC_APP_URL}/integrations/{self.id}"

    @property
    def redirect_uri(self) -> str:
        """The redirect URI for the OAuth provider."""
        return f"{self.base_url}/callback"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        """Initialize the OAuth provider.

        Args:
            client_id: Optional client ID to use instead of environment variable
            client_secret: Optional client secret to use instead of environment variable
        """
        # Get environment prefix for this provider
        env_prefix = self.id.upper().replace("-", "_")

        # Use provided credentials or fallback to environment variables
        if client_id and client_secret:
            self.client_id = client_id
            self.client_secret = client_secret
        else:
            # Get client credentials from environment
            self.client_id = os.getenv(f"{env_prefix}_CLIENT_ID")
            self.client_secret = os.getenv(f"{env_prefix}_CLIENT_SECRET")

            if not self.client_id or not self.client_secret:
                raise ValueError(
                    f"{self.id} OAuth credentials not configured. "
                    f"Either provide client_id/client_secret parameters or set "
                    f"{env_prefix}_CLIENT_ID and {env_prefix}_CLIENT_SECRET environment variables."
                )

        # Get custom scopes from environment or use defaults
        env_scopes = os.getenv(f"{env_prefix}_SCOPES")
        self.scopes = env_scopes.split(",") if env_scopes else self.default_scopes

        # Validate required endpoints
        if not self.authorization_endpoint or not self.token_endpoint:
            raise ValueError(
                f"{self.__class__.__name__} must define authorization_endpoint and token_endpoint"
            )

        # Create authlib OAuth2 client
        self.client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            scope=" ".join(self.scopes),
            response_type=self.response_type,
            grant_type=self.grant_type,
            # Additional OAuth2 parameters can be passed here
            code_challenge_method="S256" if self._use_pkce() else None,
        )

        self.logger = logger.bind(service=f"{self.__class__.__name__}")
        self.logger.info(
            f"{self.id} OAuth provider initialized",
            redirect_uri=self.redirect_uri,
            client_id=self.client_id,
            scopes=self.scopes,
        )

    def _use_pkce(self) -> bool:
        """Override to enable PKCE for providers that support/require it."""
        return False

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Override to add provider-specific authorization parameters."""
        return {}

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Override to add provider-specific token exchange parameters."""
        return {}

    async def get_authorization_url(self, state: str) -> str:
        """Get the OAuth authorization URL."""
        # Build authorization URL with authlib
        url, _ = self.client.create_authorization_url(
            self.authorization_endpoint,
            state=state,
            **self._get_additional_authorize_params(),
        )

        self.logger.info(
            "Generated OAuth authorization URL",
            provider=self.id,
            state=state,
        )
        return url

    async def exchange_code_for_token(self, code: str, state: str) -> TokenResponse:
        """Exchange authorization code for access token."""
        try:
            # Exchange code for token using authlib
            token = cast(
                dict[str, Any],
                # This is actually an async function.
                await self.client.fetch_token(
                    self.token_endpoint,
                    code=code,
                    state=state,
                    **self._get_additional_token_params(),
                ),  # type: ignore
            )

            self.logger.info(
                "Successfully acquired OAuth token",
                provider=self.id,
                state=state,
            )

            # Convert authlib token response to our TokenResponse model
            return TokenResponse(
                access_token=token["access_token"],
                refresh_token=token.get("refresh_token"),
                expires_in=token.get("expires_in", 3600),
                scope=token.get("scope", " ".join(self.scopes)),
                token_type=token.get("token_type", "Bearer"),
            )

        except Exception as e:
            self.logger.error(
                "Error exchanging code for token",
                provider=self.id,
                error=str(e),
                state=state,
            )
            raise
