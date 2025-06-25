"""Base OAuth provider using authlib for standardized OAuth2 flows."""

from typing import Any, ClassVar, Self, cast

from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import BaseModel, SecretStr

from tracecat import config
from tracecat.integrations.models import ProviderConfig, ProviderMetadata, TokenResponse
from tracecat.logger import logger


class BaseOAuthProvider:
    """Base OAuth provider using authlib for standardized OAuth2 flows."""

    id: ClassVar[str]

    # OAuth2 endpoints - to be overridden by subclasses
    _authorization_endpoint: ClassVar[str]
    _token_endpoint: ClassVar[str]

    # Default scopes - to be overridden by subclasses
    default_scopes: ClassVar[list[str]] = []

    # OAuth2 configuration
    response_type: ClassVar[str] = "code"
    grant_type: ClassVar[str] = "authorization_code"

    # Provider specific configuration schema
    config_model: ClassVar[type[BaseModel]]

    # Provider metadata
    metadata: ClassVar[ProviderMetadata]

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        scopes: list[str] | None = None,
        **kwargs,
    ):
        """Initialize the OAuth provider.

        Args:
            client_id: Optional client ID to use instead of environment variable
            client_secret: Optional client secret to use instead of environment variable
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes or self.default_scopes

        # Validate required endpoints
        if not self.authorization_endpoint or not self.token_endpoint:
            raise ValueError(
                f"{self.__class__.__name__} must define authorization_endpoint and token_endpoint"
            )

        # Create authlib OAuth2 client
        self.client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri(),
            scope=" ".join(self.scopes),
            response_type=self.response_type,
            grant_type=self.grant_type,
            # Additional OAuth2 parameters can be passed here
            code_challenge_method="S256" if self._use_pkce() else None,
        )

        self.logger = logger.bind(service=f"{self.__class__.__name__}")
        self.logger.info(
            f"{self.id} OAuth provider initialized",
            redirect_uri=self.redirect_uri(),
            client_id=self.client_id,
            scopes=self.scopes,
        )

    @classmethod
    def redirect_uri(cls) -> str:
        """The redirect URI for the OAuth provider."""
        return f"{config.TRACECAT__PUBLIC_APP_URL}/integrations/{cls.id}/callback"

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """Get the metadata for the OAuth provider."""
        return cls.config_model.model_json_schema()

    @classmethod
    def from_config(cls, config: ProviderConfig) -> Self:
        """Create an OAuth provider from a configuration."""
        logger.warning("Creating OAuth provider from configuration", config=config)
        validated_config = cls.config_model.model_validate(config.provider_config)
        return cls(
            client_id=config.client_id,
            client_secret=config.client_secret.get_secret_value(),
            **validated_config.model_dump(exclude_unset=True),
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

        self.logger.info("Generated OAuth authorization URL", provider=self.id)
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

            self.logger.info("Successfully acquired OAuth token", provider=self.id)

            # Convert authlib token response to our TokenResponse model
            return TokenResponse(
                access_token=SecretStr(token["access_token"]),
                refresh_token=SecretStr(refresh_token)
                if (refresh_token := token.get("refresh_token"))
                else None,
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

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh the access token using a refresh token."""
        try:
            # Use authlib to refresh the token
            token = cast(
                dict[str, Any],
                await self.client.refresh_token(
                    self.token_endpoint,
                    refresh_token=refresh_token,
                    **self._get_additional_token_params(),
                ),  # type: ignore
            )

            self.logger.info("Successfully refreshed OAuth token", provider=self.id)

            # Convert authlib token response to our TokenResponse model
            return TokenResponse(
                access_token=SecretStr(token["access_token"]),
                refresh_token=SecretStr(new_refresh_token)
                if (new_refresh_token := token.get("refresh_token"))
                else SecretStr(refresh_token),  # Fallback to original if not rotated
                expires_in=token.get("expires_in", 3600),
                scope=token.get("scope", " ".join(self.scopes)),
                token_type=token.get("token_type", "Bearer"),
            )

        except Exception as e:
            self.logger.error(
                "Error refreshing access token",
                provider=self.id,
                error=str(e),
            )
            raise
