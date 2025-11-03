"""Base OAuth provider using authlib for standardized OAuth2 flows."""

import asyncio
from abc import ABC
from typing import Any, ClassVar, Self, cast
from urllib.parse import urlparse

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import BaseModel, Field, SecretStr

from tracecat import config
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import (
    ProviderConfig,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.types import TokenResponse
from tracecat.logger import logger


class ClientCredentials(BaseModel):
    """Model for OAuth client credentials."""

    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str | None = Field(
        None, description="OAuth client secret (optional)"
    )


class DynamicRegistrationResult(BaseModel):
    """Result of dynamic client registration."""

    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str | None = Field(
        None, description="OAuth client secret (optional for public clients)"
    )
    auth_method: str | None = Field(
        None, description="Token endpoint authentication method"
    )


class OAuthDiscoveryResult(BaseModel):
    """Result of OAuth endpoint discovery."""

    authorization_endpoint: str = Field(
        ..., description="OAuth authorization endpoint URL"
    )
    token_endpoint: str = Field(..., description="OAuth token endpoint URL")
    token_methods: list[str] = Field(
        default_factory=list, description="Supported token endpoint auth methods"
    )
    registration_endpoint: str | None = Field(
        None, description="Dynamic client registration endpoint URL"
    )


def validate_oauth_endpoint(url: str, base_domain: str | None = None) -> None:
    """Validate an OAuth endpoint URL for security.

    Args:
        url: The endpoint URL to validate
        base_domain: Optional base domain to validate against

    Raises:
        ValueError: If the URL fails validation
    """
    parsed = urlparse(url)

    # Enforce HTTPS
    if parsed.scheme.lower() != "https":
        raise ValueError(f"OAuth endpoint must use HTTPS: {url}")

    # Check for private/internal IP addresses
    hostname = parsed.hostname
    # Validate against base domain if provided
    if base_domain and hostname:
        base_parsed = urlparse(base_domain) if base_domain.startswith("http") else None
        expected_domain = base_parsed.hostname if base_parsed else base_domain
        if hostname != expected_domain and not hostname.endswith(f".{expected_domain}"):
            raise ValueError(
                f"OAuth endpoint domain {hostname} does not match expected domain {expected_domain}"
            )


class BaseOAuthProvider(ABC):
    """Base OAuth provider containing logic common to all OAuth 2.0 providers."""

    id: ClassVar[str]
    _include_in_registry: ClassVar[bool] = True
    # OAuth2 endpoint defaults
    default_authorization_endpoint: ClassVar[str | None] = None
    default_token_endpoint: ClassVar[str | None] = None
    authorization_endpoint_help: ClassVar[str | list[str] | None] = None
    token_endpoint_help: ClassVar[str | list[str] | None] = None
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
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        **kwargs,  # Allow subclasses to pass additional parameters
    ):
        """Initialize the OAuth provider.

        Args:
            client_id: Optional client ID to use instead of environment variable
            client_secret: Optional client secret to use instead of environment variable
            scopes: Optional scopes to use (overrides defaults if provided)
            authorization_endpoint: Optional authorization endpoint override
            token_endpoint: Optional token endpoint override
            **kwargs: Additional keyword arguments (consumed by subclasses)
        """
        # kwargs allows subclasses to pass provider-specific params through from_config
        _ = kwargs  # Explicitly mark as intentionally unused
        # Initialize instance attributes with defaults
        self._client_registration_auth_method: str | None = None
        self._registration_endpoint: str | None = getattr(
            self, "_registration_endpoint", None
        )

        # Resolve client credentials, allowing subclasses to supply defaults
        credentials = self._resolve_client_credentials(client_id, client_secret)
        self.client_id = credentials.client_id
        self.client_secret = credentials.client_secret
        # Use provided scopes or fall back to defaults
        self.requested_scopes = self.scopes.default if scopes is None else scopes
        # Resolve endpoints from overrides or defaults
        resolved_authorization_endpoint = authorization_endpoint or getattr(
            self, "default_authorization_endpoint", None
        )
        resolved_token_endpoint = token_endpoint or getattr(
            self, "default_token_endpoint", None
        )

        if not resolved_authorization_endpoint or not resolved_token_endpoint:
            raise ValueError(
                f"{self.__class__.__name__} requires both authorization and token endpoints"
            )

        validate_oauth_endpoint(resolved_authorization_endpoint)
        validate_oauth_endpoint(resolved_token_endpoint)

        self._authorization_endpoint = resolved_authorization_endpoint
        self._token_endpoint = resolved_token_endpoint

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
    ) -> ClientCredentials:
        """Resolve client credentials used to initialize the OAuth client.

        Subclasses can override this to supply credentials from dynamic
        registration or other mechanisms.
        """

        if client_id is None or (isinstance(client_id, str) and not client_id.strip()):
            raise ValueError(f"{self.__class__.__name__} requires client credentials")
        if client_secret is not None and not client_secret.strip():
            client_secret = None
        return ClientCredentials(client_id=client_id, client_secret=client_secret)

    @classmethod
    def from_config(cls, config: ProviderConfig) -> Self:
        """Create an OAuth provider from a configuration."""
        return cls(
            client_id=config.client_id,
            client_secret=config.client_secret.get_secret_value()
            if config.client_secret
            else None,
            scopes=config.scopes,
            authorization_endpoint=config.authorization_endpoint,
            token_endpoint=config.token_endpoint,
        )

    @classmethod
    async def instantiate(
        cls, *, config: ProviderConfig | None = None, **kwargs: Any
    ) -> Self:
        """Async-aware factory for creating providers without blocking the event loop.

        By default this simply delegates to the synchronous constructors because most
        providers do not perform I/O during instantiation. Providers that need
        network calls (for discovery or dynamic registration) should override this
        method to implement a fully async path.
        """

        if config is not None:
            return cls.from_config(config)
        return cls(**kwargs)

    @staticmethod
    async def _submit_registration_request(
        endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a dynamic registration request asynchronously."""

        # Validate endpoint for security
        parsed = urlparse(endpoint)
        if parsed.scheme.lower() != "https":
            raise ValueError(
                f"Dynamic registration endpoint must use HTTPS for security: {endpoint}"
            )

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=10.0)
        response.raise_for_status()
        return response.json()

    def _perform_dynamic_registration(self) -> DynamicRegistrationResult:
        """Register a public client using OAuth 2.0 Dynamic Client Registration."""

        if not self._registration_endpoint:
            raise ValueError("Dynamic registration endpoint is not available")

        registration_payload = {
            "client_name": self.metadata.name,
            "redirect_uris": [self.__class__.redirect_uri()],  # type: ignore
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        }

        registration_auth_method = self._dynamic_registration_auth_method()
        if registration_auth_method:
            registration_payload["token_endpoint_auth_method"] = (
                registration_auth_method
            )

        try:
            registration_response = asyncio.run(
                self._submit_registration_request(
                    self._registration_endpoint, registration_payload
                )
            )
        except RuntimeError as exc:
            message = str(exc)
            if "event loop" in message.lower() and "running" in message.lower():
                raise RuntimeError(
                    "Dynamic client registration must be awaited. Use the async "
                    "instantiate() helper when creating providers in async contexts."
                ) from exc
            raise

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

        return DynamicRegistrationResult(
            client_id=client_id,
            client_secret=client_secret,
            auth_method=auth_method,
        )

    def _dynamic_registration_auth_method(self) -> str | None:
        """Preferred token endpoint auth method when registering dynamically."""
        return None

    def _get_token_endpoint_auth_method(self) -> str | None:
        """Return auth method to use when calling the token endpoint."""
        if self._client_registration_auth_method is not None:
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

    mcp_server_uri: ClassVar[str]
    # Optional fallback endpoints for when discovery fails
    _fallback_auth_endpoint: ClassVar[str | None] = None
    _fallback_token_endpoint: ClassVar[str | None] = None

    @staticmethod
    def _clean_credential(value: Any) -> str | None:
        """Normalize credential inputs to trimmed strings or None."""
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return None

    def __init__(
        self,
        *,
        discovered_auth_endpoint: str | None = None,
        discovered_token_endpoint: str | None = None,
        registration_endpoint: str | None = None,
        **kwargs,
    ):
        """Initialize MCP provider with dynamic endpoint discovery."""
        # Initialize logger early for discovery
        self.logger = logger.bind(service=f"{self.__class__.__name__}")

        discovery_result = self._resolve_discovery_result(
            discovered_auth_endpoint=discovered_auth_endpoint,
            discovered_token_endpoint=discovered_token_endpoint,
            registration_endpoint=registration_endpoint,
            token_methods_override=None,
        )

        self._registration_endpoint = discovery_result.registration_endpoint
        self._token_endpoint_auth_methods_supported = (
            discovery_result.token_methods or []
        )

        super().__init__(
            authorization_endpoint=discovery_result.authorization_endpoint,
            token_endpoint=discovery_result.token_endpoint,
            **kwargs,
        )

    def _resolve_discovery_result(
        self,
        *,
        discovered_auth_endpoint: str | None,
        discovered_token_endpoint: str | None,
        registration_endpoint: str | None,
        token_methods_override: list[str] | None,
    ) -> OAuthDiscoveryResult:
        """Return discovery result for initialization, performing lookup when needed."""
        if discovered_auth_endpoint and discovered_token_endpoint:
            return OAuthDiscoveryResult(
                authorization_endpoint=discovered_auth_endpoint,
                token_endpoint=discovered_token_endpoint,
                token_methods=token_methods_override or [],
                registration_endpoint=registration_endpoint,
            )

        discovered = self._discover_oauth_endpoints()

        return OAuthDiscoveryResult(
            authorization_endpoint=discovered.authorization_endpoint,
            token_endpoint=discovered.token_endpoint,
            token_methods=token_methods_override or discovered.token_methods,
            registration_endpoint=registration_endpoint
            or discovered.registration_endpoint,
        )

    @classmethod
    def _get_base_url(cls) -> str:
        """Extract HTTPS base URL from MCP server URI."""
        parsed = urlparse(cls.mcp_server_uri)

        if parsed.scheme.lower() != "https":
            raise ValueError(
                "MCP server URI must use HTTPS to ensure secure discovery and registration"
            )

        if not parsed.netloc:
            raise ValueError("MCP server URI is missing a hostname")

        return f"https://{parsed.netloc}"

    def _discover_oauth_endpoints(self) -> OAuthDiscoveryResult:
        """Discover OAuth endpoints from .well-known configuration with fallback support."""
        base_url = self._get_base_url()
        discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

        try:
            # Synchronous discovery during initialization
            with httpx.Client() as client:
                response = client.get(discovery_url, timeout=10.0)
                response.raise_for_status()
                discovery_doc = response.json()

                auth_endpoint = discovery_doc["authorization_endpoint"]
                token_endpoint = discovery_doc["token_endpoint"]
                token_methods = discovery_doc.get(
                    "token_endpoint_auth_methods_supported", []
                )

                # Validate discovered endpoints for security
                base_domain = urlparse(base_url).hostname
                validate_oauth_endpoint(auth_endpoint, base_domain)
                validate_oauth_endpoint(token_endpoint, base_domain)

                registration_endpoint = discovery_doc.get("registration_endpoint")
                if registration_endpoint:
                    validate_oauth_endpoint(registration_endpoint, base_domain)

                self.logger.info(
                    "Discovered OAuth endpoints",
                    provider=self.id,
                    authorization=auth_endpoint,
                    token=token_endpoint,
                )
                return OAuthDiscoveryResult(
                    authorization_endpoint=auth_endpoint,
                    token_endpoint=token_endpoint,
                    token_methods=token_methods,
                    registration_endpoint=registration_endpoint,
                )
        except Exception as e:
            # Check if subclass provides fallback endpoints
            if self._fallback_auth_endpoint and self._fallback_token_endpoint:
                validate_oauth_endpoint(self._fallback_auth_endpoint)
                validate_oauth_endpoint(self._fallback_token_endpoint)
                self.logger.info(
                    "Using fallback OAuth endpoints",
                    provider=self.id,
                    authorization=self._fallback_auth_endpoint,
                    token=self._fallback_token_endpoint,
                )
                return OAuthDiscoveryResult(
                    authorization_endpoint=self._fallback_auth_endpoint,
                    token_endpoint=self._fallback_token_endpoint,
                    token_methods=[],
                    registration_endpoint=None,
                )

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

    @classmethod
    async def _discover_oauth_endpoints_async(
        cls, logger_instance
    ) -> OAuthDiscoveryResult:
        """Async discovery counterpart used in event-loop contexts."""

        base_url = cls._get_base_url()
        discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(discovery_url, timeout=10.0)
            response.raise_for_status()
            discovery_doc = response.json()

            authorization_endpoint = discovery_doc["authorization_endpoint"]
            token_endpoint = discovery_doc["token_endpoint"]
            token_methods = discovery_doc.get(
                "token_endpoint_auth_methods_supported", []
            )
            registration_endpoint = discovery_doc.get("registration_endpoint")

            # Validate discovered endpoints for security
            base_domain = urlparse(base_url).hostname
            validate_oauth_endpoint(authorization_endpoint, base_domain)
            validate_oauth_endpoint(token_endpoint, base_domain)
            if registration_endpoint:
                validate_oauth_endpoint(registration_endpoint, base_domain)

            logger_instance.info(
                "Discovered OAuth endpoints",
                provider=cls.id,
                authorization=authorization_endpoint,
                token=token_endpoint,
            )
            return OAuthDiscoveryResult(
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                token_methods=token_methods,
                registration_endpoint=registration_endpoint,
            )
        except Exception as e:
            if cls._fallback_auth_endpoint and cls._fallback_token_endpoint:
                token_methods = []
                logger_instance.info(
                    "Using fallback OAuth endpoints",
                    provider=cls.id,
                    authorization=cls._fallback_auth_endpoint,
                    token=cls._fallback_token_endpoint,
                )
                return OAuthDiscoveryResult(
                    authorization_endpoint=cls._fallback_auth_endpoint,
                    token_endpoint=cls._fallback_token_endpoint,
                    token_methods=token_methods,
                    registration_endpoint=None,
                )

            logger_instance.error(
                "Failed to discover OAuth endpoints",
                provider=cls.id,
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
    ) -> ClientCredentials:
        resolved_client_id = self._clean_credential(client_id)
        resolved_client_secret = self._clean_credential(client_secret)

        if not resolved_client_id and self._registration_endpoint:
            registration_result = self._perform_dynamic_registration()
            resolved_client_id = registration_result.client_id
            resolved_client_secret = registration_result.client_secret

        if not resolved_client_id:
            raise ValueError("Missing hosted client credential: client_id")

        # Secrets are optional for public clients (token endpoint auth method "none").
        return ClientCredentials(
            client_id=resolved_client_id, client_secret=resolved_client_secret
        )

    @classmethod
    def _select_dynamic_registration_auth_method(cls, methods: list[str]) -> str | None:
        """Select the preferred token endpoint auth method for registration."""

        if "client_secret_post" in methods:
            return "client_secret_post"
        if "client_secret_basic" in methods:
            return "client_secret_basic"
        if "none" in methods:
            return "none"
        return None

    @classmethod
    async def _perform_dynamic_registration_async(
        cls,
        *,
        registration_endpoint: str,
        registration_auth_method: str | None,
        logger_instance,
    ) -> DynamicRegistrationResult:
        """Execute dynamic registration without blocking the event loop."""

        registration_payload = {
            "client_name": cls.metadata.name,
            "redirect_uris": [cls.redirect_uri()],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        }
        if registration_auth_method:
            registration_payload["token_endpoint_auth_method"] = (
                registration_auth_method
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    registration_endpoint, json=registration_payload, timeout=10.0
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger_instance.error(
                "Dynamic registration failed",
                provider=cls.id,
                error=str(exc),
                registration_endpoint=registration_endpoint,
            )
            raise

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

        logger_instance.info(
            "Registered OAuth client dynamically",
            provider=cls.id,
            registration_endpoint=registration_endpoint,
            client_id=client_id,
        )

        return DynamicRegistrationResult(
            client_id=client_id,
            client_secret=client_secret,
            auth_method=auth_method,
        )

    def _dynamic_registration_auth_method(self) -> str | None:
        return self._select_dynamic_registration_auth_method(
            self._token_endpoint_auth_methods_supported
        )

    def _get_token_endpoint_auth_method(self) -> str | None:
        if self._client_registration_auth_method:
            return self._client_registration_auth_method
        methods = self._token_endpoint_auth_methods_supported or []
        if self.client_secret:
            for candidate in ("client_secret_post", "client_secret_basic"):
                if candidate in methods:
                    return candidate
        elif "none" in methods:
            return "none"
        return super()._get_token_endpoint_auth_method()

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add MCP-specific authorization parameters.

        The resource parameter identifies the MCP server that the token will be used with.
        """
        params = super()._get_additional_authorize_params()
        params["resource"] = self.mcp_server_uri
        return params

    @classmethod
    async def instantiate(
        cls, *, config: ProviderConfig | None = None, **kwargs: Any
    ) -> Self:
        """Instantiate MCP providers without blocking the event loop."""

        logger_instance = logger.bind(service=f"{cls.__name__}")

        discovery_result = await cls._discover_oauth_endpoints_async(logger_instance)

        scopes = (
            config.scopes
            if config and config.scopes is not None
            else kwargs.get("scopes")
        )

        if config:
            client_id = cls._clean_credential(config.client_id)
            client_secret = cls._clean_credential(config.client_secret)
        else:
            client_id = cls._clean_credential(kwargs.get("client_id"))
            client_secret = cls._clean_credential(kwargs.get("client_secret"))

        registration_auth_method = None
        if not client_id and discovery_result.registration_endpoint:
            registration_auth_method = cls._select_dynamic_registration_auth_method(
                discovery_result.token_methods
            )
            registration_result = await cls._perform_dynamic_registration_async(
                registration_endpoint=discovery_result.registration_endpoint,
                registration_auth_method=registration_auth_method,
                logger_instance=logger_instance,
            )
            client_id = registration_result.client_id
            client_secret = registration_result.client_secret
            registration_auth_method = registration_result.auth_method

        if not client_id:
            raise ValueError("Missing hosted client credential: client_id")

        init_kwargs = dict(kwargs)
        init_kwargs.update(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            authorization_endpoint=discovery_result.authorization_endpoint,
            token_endpoint=discovery_result.token_endpoint,
            registration_endpoint=discovery_result.registration_endpoint,
        )

        provider = cls(**init_kwargs)

        if registration_auth_method:
            provider._client_registration_auth_method = registration_auth_method

        return provider

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Add MCP-specific token exchange parameters.

        The resource parameter must be included in token requests per MCP spec.
        """
        params = super()._get_additional_token_params()
        params["resource"] = self.mcp_server_uri
        return params
