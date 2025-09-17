"""Base OAuth provider using authlib for standardized OAuth2 flows."""

from abc import ABC
from typing import Any, ClassVar, Self, cast
from urllib.parse import urlparse

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import BaseModel, SecretStr

from tracecat import config
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import (
    ProviderConfig,
    ProviderMetadata,
    ProviderScopes,
    TokenResponse,
)
from tracecat.logger import logger


class BaseOAuthProvider(ABC):
    """Base OAuth provider containing logic common to all OAuth 2.0 providers."""

    id: ClassVar[str]
    _include_in_registry: ClassVar[bool] = True
    # OAuth2 endpoints
    _authorization_endpoint: ClassVar[str]
    _token_endpoint: ClassVar[str]
    scopes: ClassVar[ProviderScopes]
    grant_type: ClassVar[OAuthGrantType]
    # Provider specific configs
    config_model: ClassVar[type[BaseModel] | None] = None
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
            scopes: Optional scopes to use (overrides defaults if provided)
        """
        # Resolve client credentials, allowing subclasses to supply defaults
        self.client_id, self.client_secret = self._resolve_client_credentials(
            client_id, client_secret
        )
        # Use provided scopes or fall back to defaults
        self.requested_scopes = self.scopes.default if scopes is None else scopes

        # Validate required endpoints
        if not self.authorization_endpoint or not self.token_endpoint:
            raise ValueError(
                f"{self.__class__.__name__} must define authorization_endpoint and token_endpoint"
            )
        if not self.id == self.metadata.id:
            raise ValueError(f"{self.__class__.__name__} id must match metadata.id")

        # Create base client kwargs
        client_kwargs = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": self.grant_type,
        }

        token_auth_method = self._get_token_endpoint_auth_method()
        if token_auth_method:
            client_kwargs["token_endpoint_auth_method"] = token_auth_method

        # Only add scope if not empty
        if self.requested_scopes:
            client_kwargs["scope"] = " ".join(self.requested_scopes)

        # Let subclasses add grant-specific parameters
        client_kwargs.update(self._get_client_kwargs())

        self.client = AsyncOAuth2Client(**client_kwargs)

        self.logger = logger.bind(service=f"{self.__class__.__name__}")
        self.logger.info(
            "OAuth provider initialized",
            provider=self.id,
            client_id=self.client_id,
            scopes=self.requested_scopes,
            grant_type=self.grant_type,
        )

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint

    @classmethod
    def schema(cls) -> dict[str, Any] | None:
        """Get the metadata for the OAuth provider."""
        return cls.config_model.model_json_schema() if cls.config_model else None

    def _resolve_client_credentials(
        self, client_id: str | None, client_secret: str | None
    ) -> tuple[str | None, str | None]:
        """Resolve client credentials used to initialize the OAuth client.

        Subclasses can override this to supply credentials from dynamic
        registration or other mechanisms.
        """

        if client_id is None or (isinstance(client_id, str) and not client_id.strip()):
            raise ValueError(f"{self.__class__.__name__} requires client credentials")
        if client_secret is not None and not client_secret.strip():
            client_secret = None
        return client_id, client_secret

    @classmethod
    def from_config(cls, config: ProviderConfig) -> Self:
        """Create an OAuth provider from a configuration."""
        if cls.config_model:
            model = cls.config_model.model_validate(config.provider_config)
            validated_config = model.model_dump(exclude_unset=True)
        else:
            validated_config = {}
        return cls(
            client_id=config.client_id,
            client_secret=config.client_secret.get_secret_value()
            if config.client_secret
            else None,
            scopes=config.scopes,
            **validated_config,
        )

    def _perform_dynamic_registration(self) -> tuple[str, str | None]:
        """Register a public client using OAuth 2.0 Dynamic Client Registration."""

        if not getattr(self, "_registration_endpoint", None):
            raise ValueError("Dynamic registration endpoint is not available")

        registration_payload = {
            "client_name": self.metadata.name,
            "redirect_uris": [self.redirect_uri()],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        }

        registration_auth_method = self._dynamic_registration_auth_method()
        if registration_auth_method:
            registration_payload["token_endpoint_auth_method"] = (
                registration_auth_method
            )

        with httpx.Client() as client:
            response = client.post(
                self._registration_endpoint,
                json=registration_payload,
                timeout=10.0,
            )
            response.raise_for_status()
            registration_response = response.json()

        client_id = registration_response.get("client_id")
        if not client_id:
            raise ValueError(
                "Dynamic client registration response did not include client_id"
            )

        client_secret = registration_response.get("client_secret")

        auth_method = (
            registration_response.get("token_endpoint_auth_method")
            or registration_auth_method
        )
        if auth_method:
            self._client_registration_auth_method = auth_method

        self.logger.info(
            "Registered OAuth client dynamically",
            provider=self.id,
            registration_endpoint=self._registration_endpoint,
            client_id=client_id,
        )

        return client_id, client_secret

    def _dynamic_registration_auth_method(self) -> str | None:
        """Preferred token endpoint auth method when registering dynamically."""
        return None

    def _get_token_endpoint_auth_method(self) -> str | None:
        """Return auth method to use when calling the token endpoint."""
        if hasattr(self, "_client_registration_auth_method"):
            return self._client_registration_auth_method
        if self.client_secret:
            return "client_secret_basic"
        return None

    def _get_client_kwargs(self) -> dict[str, Any]:
        """Override to add grant-specific client parameters."""
        return {}

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Override to add provider-specific token exchange parameters."""
        return {}


class AuthorizationCodeOAuthProvider(BaseOAuthProvider):
    """Base OAuth provider for authorization code flow."""

    # OAuth2 configuration for authorization code flow
    response_type: ClassVar[str] = "code"
    grant_type: ClassVar[OAuthGrantType] = OAuthGrantType.AUTHORIZATION_CODE

    def _get_client_kwargs(self) -> dict[str, Any]:
        """Add authorization code flow specific parameters."""
        return {
            "redirect_uri": self.redirect_uri(),
            "response_type": self.response_type,
            "code_challenge_method": "S256" if self._use_pkce() else None,
        }

    @classmethod
    def redirect_uri(cls) -> str:
        """The redirect URI for the OAuth provider."""
        return f"{config.TRACECAT__PUBLIC_APP_URL}/integrations/callback"

    def _use_pkce(self) -> bool:
        """Override to enable PKCE for providers that support/require it."""
        return False

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Override to add provider-specific authorization parameters."""
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
                scope=token.get("scope", " ".join(self.requested_scopes)),
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
                scope=token.get("scope", " ".join(self.requested_scopes)),
                token_type=token.get("token_type", "Bearer"),
            )

        except Exception as e:
            self.logger.error(
                "Error refreshing access token",
                provider=self.id,
                error=str(e),
            )
            raise


class ClientCredentialsOAuthProvider(BaseOAuthProvider):
    """Base OAuth provider for client credentials flow."""

    # OAuth2 configuration for client credentials flow
    grant_type: ClassVar[OAuthGrantType] = OAuthGrantType.CLIENT_CREDENTIALS

    async def get_client_credentials_token(self) -> TokenResponse:
        """Get token using client credentials flow."""
        try:
            # Get token using client credentials flow
            token = cast(
                dict[str, Any],
                await self.client.fetch_token(
                    self.token_endpoint,
                    grant_type="client_credentials",
                    **self._get_additional_token_params(),
                ),  # type: ignore
            )

            self.logger.info(
                "Successfully acquired client credentials token", provider=self.id
            )

            # Convert authlib token response to our TokenResponse model
            return TokenResponse(
                access_token=SecretStr(token["access_token"]),
                refresh_token=None,  # Client credentials flow doesn't use refresh tokens
                expires_in=token.get("expires_in", 3600),
                scope=token.get("scope", " ".join(self.requested_scopes)),
                token_type=token.get("token_type", "Bearer"),
            )

        except Exception as e:
            self.logger.error(
                "Error acquiring client credentials token",
                provider=self.id,
                error=str(e),
            )
            raise


class MCPAuthProvider(AuthorizationCodeOAuthProvider):
    """Base OAuth provider for Model Context Protocol (MCP) servers using OAuth 2.1.

    MCP OAuth follows OAuth 2.1 standards with:
    - PKCE required for authorization code flow
    - Resource parameter to identify the MCP server
    - Flexible scope handling (server determines granted scopes)
    - Dynamic discovery of OAuth endpoints
    - Optional dynamic client registration
    """

    _mcp_server_uri: ClassVar[str]
    token_endpoint_auth_methods_supported: ClassVar[list[str]] = []

    def __init__(self, **kwargs):
        """Initialize MCP provider with dynamic endpoint discovery."""
        # Initialize logger early for discovery
        self.logger = logger.bind(service=f"{self.__class__.__name__}")

        # Discover OAuth endpoints before parent initialization
        self._discover_oauth_endpoints()
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        """Return the discovered authorization endpoint."""
        return self._discovered_auth_endpoint

    @property
    def token_endpoint(self) -> str:
        """Return the discovered token endpoint."""
        return self._discovered_token_endpoint

    def _get_base_url(self) -> str:
        """Extract base URL from MCP server URI."""
        parsed = urlparse(self._mcp_server_uri)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _discover_oauth_endpoints(self) -> None:
        """Discover OAuth endpoints from .well-known configuration with fallback support."""
        base_url = self._get_base_url()
        self._registration_endpoint = None
        discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

        try:
            # Synchronous discovery during initialization
            with httpx.Client() as client:
                response = client.get(discovery_url, timeout=10.0)
                response.raise_for_status()
                discovery_doc = response.json()

                # Store discovered endpoints as instance variables
                self._discovered_auth_endpoint = discovery_doc["authorization_endpoint"]
                self._discovered_token_endpoint = discovery_doc["token_endpoint"]
                self._token_endpoint_auth_methods_supported = discovery_doc.get(
                    "token_endpoint_auth_methods_supported", []
                )

                # Store registration endpoint if available
                self._registration_endpoint = discovery_doc.get("registration_endpoint")

                self.logger.info(
                    "Discovered OAuth endpoints",
                    provider=self.id,
                    authorization=self._discovered_auth_endpoint,
                    token=self._discovered_token_endpoint,
                )
        except Exception as e:
            # Check if subclass provides fallback endpoints
            if hasattr(self, "_fallback_auth_endpoint") and hasattr(
                self, "_fallback_token_endpoint"
            ):
                self._discovered_auth_endpoint = self._fallback_auth_endpoint
                self._discovered_token_endpoint = self._fallback_token_endpoint
                self._token_endpoint_auth_methods_supported = getattr(
                    self, "token_endpoint_auth_methods_supported", []
                )
                self.logger.info(
                    "Using fallback OAuth endpoints",
                    provider=self.id,
                    authorization=self._discovered_auth_endpoint,
                    token=self._discovered_token_endpoint,
                )
            else:
                self.logger.error(
                    "Failed to discover OAuth endpoints",
                    provider=self.id,
                    error=str(e),
                    discovery_url=discovery_url,
                )
                raise ValueError(
                    f"Could not discover OAuth endpoints from {discovery_url} "
                    f"and no fallback endpoints provided"
                ) from e

    def _use_pkce(self) -> bool:
        """PKCE is mandatory for OAuth 2.1 compliance."""
        return True

    def _resolve_client_credentials(
        self, client_id: str | None, client_secret: str | None
    ) -> tuple[str | None, str | None]:
        resolved_client_id = client_id if client_id and client_id.strip() else None
        resolved_client_secret = (
            client_secret if client_secret and client_secret.strip() else None
        )

        # Attempt dynamic client registration when no credentials are provided.
        if resolved_client_id is None and self._registration_endpoint:
            resolved_client_id, resolved_client_secret = (
                self._perform_dynamic_registration()
            )

        if resolved_client_id is None:
            raise ValueError("Missing hosted client credential: client_id")

        # Secrets are optional for public clients (token endpoint auth method "none").
        return resolved_client_id, resolved_client_secret

    def _dynamic_registration_auth_method(self) -> str | None:
        methods = getattr(self, "_token_endpoint_auth_methods_supported", [])
        if "client_secret_post" in methods:
            return "client_secret_post"
        if "client_secret_basic" in methods:
            return "client_secret_basic"
        if "none" in methods:
            return "none"
        return None

    def _get_token_endpoint_auth_method(self) -> str | None:
        if hasattr(self, "_client_registration_auth_method"):
            return self._client_registration_auth_method
        methods = getattr(self, "_token_endpoint_auth_methods_supported", [])
        if self.client_secret:
            if "client_secret_post" in methods:
                return "client_secret_post"
            if "client_secret_basic" in methods:
                return "client_secret_basic"
        else:
            if "none" in methods:
                return "none"
        return super()._get_token_endpoint_auth_method()

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add MCP-specific authorization parameters.

        The resource parameter identifies the MCP server that the token will be used with.
        """
        params = super()._get_additional_authorize_params()
        params["resource"] = self._mcp_server_uri
        return params

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Add MCP-specific token exchange parameters.

        The resource parameter must be included in token requests per MCP spec.
        """
        params = super()._get_additional_token_params()
        params["resource"] = self._mcp_server_uri
        return params
