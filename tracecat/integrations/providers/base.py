"""Base OAuth provider using authlib for standardized OAuth2 flows."""

import asyncio
import ipaddress
import json
import secrets
import socket
from abc import ABC
from collections.abc import Sequence
from json import JSONDecodeError
from typing import Any, ClassVar, Self
from urllib.parse import urlparse, urlunparse

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from pydantic import BaseModel, Field, SecretStr

from tracecat import config
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
from tracecat.integrations.schemas import (
    ProviderConfig,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.types import (
    DCRResponse,
    OAuthServerMetadata,
    TokenResponse,
)
from tracecat.logger import logger

SocketAddress = tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes]
SocketInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, SocketAddress]
_OAUTH_AUTHORIZATION_SERVER_WELL_KNOWN = "/.well-known/oauth-authorization-server"


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

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"OAuth endpoint must include a hostname: {url}")
    normalized_hostname = hostname.rstrip(".").lower()
    if normalized_hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("OAuth endpoint host is not allowed")
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        address = None
    if address and _is_disallowed_oauth_address(address):
        raise ValueError("OAuth endpoint host is not allowed")

    # Validate against base domain if provided
    if base_domain:
        base_parsed = urlparse(base_domain) if base_domain.startswith("http") else None
        expected_domain = (base_parsed.hostname if base_parsed else base_domain) or ""
        expected_domain = expected_domain.rstrip(".").lower()
        if normalized_hostname != expected_domain and not normalized_hostname.endswith(
            f".{expected_domain}"
        ):
            raise ValueError(
                f"OAuth endpoint domain {hostname} does not match expected domain {expected_domain}"
            )


def oauth_authorization_server_metadata_urls(issuer: str) -> list[str]:
    """Build RFC 8414 authorization-server metadata URLs for an issuer."""

    parsed = urlparse(issuer.strip().rstrip("/"))
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return []
    if parsed.params or parsed.query or parsed.fragment:
        return []

    path = parsed.path.rstrip("/")
    if path == _OAUTH_AUTHORIZATION_SERVER_WELL_KNOWN or path.startswith(
        f"{_OAUTH_AUTHORIZATION_SERVER_WELL_KNOWN}/"
    ):
        return [urlunparse(("https", parsed.netloc, path, "", "", ""))]

    metadata_path = (
        _OAUTH_AUTHORIZATION_SERVER_WELL_KNOWN
        if not path
        else f"{_OAUTH_AUTHORIZATION_SERVER_WELL_KNOWN}{path}"
    )
    urls = [urlunparse(("https", parsed.netloc, metadata_path, "", "", ""))]
    if path:
        legacy_path = f"{path}{_OAUTH_AUTHORIZATION_SERVER_WELL_KNOWN}"
        urls.append(urlunparse(("https", parsed.netloc, legacy_path, "", "", "")))
    return urls


def _is_disallowed_oauth_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    # ``is_global`` is the authoritative "publicly routable" check and rejects
    # ranges the explicit flags miss (e.g. CGNAT 100.64.0.0/10, TEST-NET, and
    # other non-global assignments). Keep the explicit flags for clarity and to
    # guard against any address class not yet covered by ``is_global``.
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _validate_oauth_resolved_addresses(infos: Sequence[SocketInfo]) -> None:
    if not infos:
        raise ValueError("OAuth endpoint host could not be resolved")
    for *_, sockaddr in infos:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except (IndexError, ValueError) as exc:
            raise ValueError("OAuth endpoint host is not allowed") from exc
        if _is_disallowed_oauth_address(address):
            raise ValueError("OAuth endpoint host is not allowed")


def validate_oauth_endpoint_resolves_public(
    url: str, base_domain: str | None = None
) -> None:
    """Validate URL and require DNS resolution to public IP addresses."""

    validate_oauth_endpoint(url, base_domain=base_domain)
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"OAuth endpoint must include a hostname: {url}")
    port = parsed.port or 443
    try:
        infos = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ValueError("OAuth endpoint host could not be resolved") from exc
    _validate_oauth_resolved_addresses(infos)


async def validate_oauth_endpoint_resolves_public_async(
    url: str, base_domain: str | None = None
) -> None:
    """Async wrapper for DNS-backed OAuth endpoint validation."""

    validate_oauth_endpoint(url, base_domain=base_domain)
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"OAuth endpoint must include a hostname: {url}")
    port = parsed.port or 443
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise ValueError("OAuth endpoint host could not be resolved") from exc
    _validate_oauth_resolved_addresses(infos)


class CustomOAuthProviderMixin:
    """Mixin for dynamically created custom OAuth providers."""

    _include_in_registry: ClassVar[bool] = False
    config_model: ClassVar[type[BaseModel] | None] = None

    @classmethod
    def schema(cls) -> dict[str, Any] | None:  # pragma: no cover - trivial override
        return None


class BaseMCPProvider(ABC):
    """Base metadata contract for platform-provided MCP connections."""

    id: ClassVar[str]
    metadata: ClassVar[ProviderMetadata]
    mcp_server_uri: ClassVar[str] = ""
    server_type: ClassVar[str] = "http"
    auth_type: ClassVar[MCPAuthType]


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
            scopes=self.requested_scopes,
            grant_type=self.grant_type,
            code_challenge_method=client_kwargs.get("code_challenge_method"),
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
    ) -> DCRResponse:
        """Send a dynamic registration request asynchronously."""

        await validate_oauth_endpoint_resolves_public_async(endpoint)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=10.0)
        response.raise_for_status()
        return DCRResponse.model_validate(response.json())

    def _perform_dynamic_registration(self) -> DynamicRegistrationResult:
        """Register a public client using OAuth 2.0 Dynamic Client Registration."""

        if not self._registration_endpoint:
            raise ValueError("Dynamic registration endpoint is not available")

        registration_payload = {
            "client_name": self.metadata.name,
            "redirect_uris": [self.__class__.redirect_uri()],  # pyright: ignore[reportAttributeAccessIssue]
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

        auth_method = (
            registration_response.token_endpoint_auth_method or registration_auth_method
        )
        if auth_method:
            self._client_registration_auth_method = auth_method

        self.logger.info(
            "Registered OAuth client dynamically",
            provider=self.id,
        )

        return DynamicRegistrationResult(
            client_id=registration_response.client_id,
            client_secret=registration_response.client_secret,
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

    async def get_authorization_url(self, state: str) -> tuple[str, str | None]:
        """Get the OAuth authorization URL.

        Returns:
            Tuple of (authorization_url, code_verifier)
            code_verifier is None if PKCE is not used
        """
        # Build authorization URL with authlib
        additional_params = self._get_additional_authorize_params()

        # Manually generate PKCE parameters if enabled for this provider
        code_verifier = None
        if self._use_pkce():
            # Generate code_verifier (43-128 characters, base64url encoded)
            code_verifier = secrets.token_urlsafe(32)  # Generates 43 characters
            # Generate code_challenge from verifier
            code_challenge = create_s256_code_challenge(code_verifier)
            additional_params["code_challenge"] = code_challenge
            additional_params["code_challenge_method"] = "S256"

        url, _ = self.client.create_authorization_url(
            self.authorization_endpoint,
            state=state,
            **additional_params,
        )

        self.logger.info(
            "Generated OAuth authorization URL",
            provider=self.id,
            has_code_verifier=code_verifier is not None,
            use_pkce=self._use_pkce(),
        )
        return url, code_verifier

    async def exchange_code_for_token(
        self, code: str, state: str, code_verifier: str | None = None
    ) -> TokenResponse:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from OAuth provider
            state: State parameter from authorization request
            code_verifier: PKCE code verifier (required if PKCE was used)
        """
        try:
            # Build token request params
            token_params = self._get_additional_token_params()
            if code_verifier:
                token_params["code_verifier"] = code_verifier

            token = TokenResponse.from_oauth_response(
                await self.client.fetch_token(
                    self.token_endpoint,
                    code=code,
                    state=state,
                    **token_params,
                ),
                default_scope=" ".join(self.requested_scopes),
            )

            self.logger.info(
                "Successfully acquired OAuth token",
                provider=self.id,
                used_pkce=code_verifier is not None,
            )

            return token

        except Exception as e:
            self.logger.error(
                "Error exchanging code for token",
                provider=self.id,
                error=str(e),
            )
            raise

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh the access token using a refresh token."""
        try:
            token = TokenResponse.from_oauth_response(
                await self.client.refresh_token(
                    self.token_endpoint,
                    refresh_token=refresh_token,
                    **self._get_additional_token_params(),
                ),
                default_refresh_token=refresh_token,
                default_scope=" ".join(self.requested_scopes),
            )

            self.logger.info("Successfully refreshed OAuth token", provider=self.id)

            return token

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
            token = TokenResponse.from_oauth_response(
                await self.client.fetch_token(
                    self.token_endpoint,
                    grant_type="client_credentials",
                    **self._get_additional_token_params(),
                ),
                default_scope=" ".join(self.requested_scopes),
            )

            self.logger.info(
                "Successfully acquired client credentials token", provider=self.id
            )

            return token

        except Exception as e:
            self.logger.error(
                "Error acquiring client credentials token",
                provider=self.id,
                error=str(e),
            )
            raise


class ServiceAccountOAuthProvider(ClientCredentialsOAuthProvider):
    """Base provider for service account style OAuth flows.

    Service accounts typically use a JSON key containing a private key to mint JWT
    assertions rather than client secrets issued by an authorization server. This
    base class loads and validates the JSON payload while allowing subclasses to
    implement provider-specific token acquisition.
    """

    def __init__(
        self,
        *,
        subject: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._service_account_info: dict[str, Any] | None = None
        self._service_account_subject: str | None = subject
        super().__init__(**kwargs)

    @property
    def service_account_info(self) -> dict[str, Any]:
        if self._service_account_info is None:
            raise ValueError("Service account credentials have not been loaded.")
        return self._service_account_info

    @property
    def service_account_subject(self) -> str | None:
        return self._service_account_subject

    def _resolve_client_credentials(
        self, client_id: str | None, client_secret: str | None
    ) -> ClientCredentials:
        info = self._load_service_account_info(client_secret)
        derived_client_id = self._derive_client_id(info, client_id)
        self._service_account_info = info
        return ClientCredentials(client_id=derived_client_id, client_secret=None)

    def _load_service_account_info(self, client_secret: str | None) -> dict[str, Any]:
        if client_secret is None or not client_secret.strip():
            raise ValueError(
                "Service account credentials (JSON) are required for this provider."
            )
        try:
            parsed = json.loads(client_secret)
        except JSONDecodeError as exc:
            raise ValueError("Service account credentials must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Service account credentials must be a JSON object.")

        subject = self._extract_subject(parsed)
        if subject and self._service_account_subject is None:
            self._service_account_subject = subject

        return parsed

    def _extract_subject(self, info: dict[str, Any]) -> str | None:
        subject = info.get("subject")
        if subject is None:
            return None
        return str(subject).strip() or None

    def _derive_client_id(
        self, info: dict[str, Any], configured_client_id: str | None
    ) -> str:
        if configured_client_id is None or not configured_client_id.strip():
            raise ValueError(
                "Client ID (typically the service account email) is required."
            )
        return configured_client_id.strip()


class MCPAuthProvider(BaseMCPProvider, AuthorizationCodeOAuthProvider):
    """Base OAuth provider for Model Context Protocol (MCP) servers using OAuth 2.1.

    MCP OAuth follows OAuth 2.1 standards with:
    - PKCE required for authorization code flow
    - Resource parameter to identify the MCP server
    - Flexible scope handling (server determines granted scopes)
    - Dynamic discovery of OAuth endpoints
    - Optional dynamic client registration
    """

    mcp_server_uri: ClassVar[str]
    auth_type: ClassVar[MCPAuthType] = MCPAuthType.OAUTH2
    oauth_endpoint_allowed_hosts: ClassVar[frozenset[str]] = frozenset()
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
        token_methods: list[str] | None = None,
        **kwargs,
    ):
        """Initialize MCP provider with dynamic endpoint discovery."""
        # Initialize logger early for discovery
        self.logger = logger.bind(service=f"{self.__class__.__name__}")

        # Extract endpoint overrides from kwargs if not explicitly provided
        # This allows users to override defaults via authorization_endpoint/token_endpoint
        if not discovered_auth_endpoint:
            discovered_auth_endpoint = kwargs.pop("authorization_endpoint", None)
        if not discovered_token_endpoint:
            discovered_token_endpoint = kwargs.pop("token_endpoint", None)

        discovery_result = self._resolve_discovery_result(
            discovered_auth_endpoint=discovered_auth_endpoint,
            discovered_token_endpoint=discovered_token_endpoint,
            registration_endpoint=registration_endpoint,
            token_methods_override=token_methods,
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

        discovered = self._discover_oauth_endpoints(
            discovered_auth_endpoint=discovered_auth_endpoint,
            discovered_token_endpoint=discovered_token_endpoint,
        )

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

    @classmethod
    def _get_resource_uri(cls) -> str:
        """Return the canonical MCP resource URI for OAuth resource indicators."""

        parsed = urlparse(cls.mcp_server_uri)
        if parsed.scheme.lower() != "https":
            raise ValueError(
                "MCP server URI must use HTTPS to ensure secure discovery and registration"
            )
        if not parsed.hostname:
            raise ValueError("MCP server URI is missing a hostname")
        if parsed.fragment:
            raise ValueError("MCP server URI cannot include a fragment")

        host = parsed.hostname.lower()
        # ``urlparse`` strips the brackets from IPv6 literals; restore them so
        # the rebuilt netloc stays a valid authority (e.g. ``[::1]:443``).
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        path = parsed.path if parsed.path else ""
        return urlunparse(("https", netloc, path, "", parsed.query, ""))

    @classmethod
    def _validate_discovered_oauth_endpoint(
        cls, endpoint: str, base_domain: str | None
    ) -> None:
        """Validate an endpoint from MCP OAuth discovery.

        By default, discovered endpoints must live on the MCP resource server host
        or its subdomains. Some providers publish their OAuth endpoints on a
        separate exact host; those providers can opt in with
        ``oauth_endpoint_allowed_hosts`` without relaxing validation globally.
        """

        try:
            validate_oauth_endpoint(endpoint, base_domain)
        except ValueError:
            hostname = urlparse(endpoint).hostname
            if hostname and hostname in cls.oauth_endpoint_allowed_hosts:
                validate_oauth_endpoint(endpoint)
                return
            raise

    def _discover_oauth_endpoints(
        self,
        *,
        discovered_auth_endpoint: str | None = None,
        discovered_token_endpoint: str | None = None,
    ) -> OAuthDiscoveryResult:
        """Discover OAuth endpoints from .well-known configuration with fallback support."""
        base_url = self._get_base_url()
        discovery_url = oauth_authorization_server_metadata_urls(base_url)[0]

        try:
            # Synchronous discovery during initialization
            validate_oauth_endpoint_resolves_public(discovery_url)
            with httpx.Client() as client:
                response = client.get(discovery_url, timeout=10.0)
                response.raise_for_status()
                metadata = OAuthServerMetadata.from_json(response.json())
                if metadata is None or not metadata.is_complete:
                    raise ValueError("OAuth discovery document is missing endpoints")
                auth_endpoint = metadata.authorization_endpoint
                token_endpoint = metadata.token_endpoint
                if auth_endpoint is None or token_endpoint is None:
                    raise ValueError("OAuth discovery document is missing endpoints")
                token_methods = metadata.token_endpoint_auth_methods_supported

                # Validate discovered endpoints for security
                base_domain = urlparse(base_url).hostname
                self._validate_discovered_oauth_endpoint(auth_endpoint, base_domain)
                self._validate_discovered_oauth_endpoint(token_endpoint, base_domain)

                registration_endpoint = metadata.registration_endpoint
                if registration_endpoint:
                    self._validate_discovered_oauth_endpoint(
                        registration_endpoint, base_domain
                    )

                self.logger.info(
                    "Discovered OAuth endpoints",
                    provider=self.id,
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
                )
                return OAuthDiscoveryResult(
                    authorization_endpoint=self._fallback_auth_endpoint,
                    token_endpoint=self._fallback_token_endpoint,
                    token_methods=[],
                    registration_endpoint=None,
                )

            # If _fallback_token_endpoint is None, check if configured endpoints are available
            if self._fallback_token_endpoint is None:
                # Check for configured endpoints from init params or class defaults
                auth_endpoint = (
                    discovered_auth_endpoint
                    or self._fallback_auth_endpoint
                    or getattr(self, "default_authorization_endpoint", None)
                )
                token_endpoint = discovered_token_endpoint or getattr(
                    self, "default_token_endpoint", None
                )

                if auth_endpoint and token_endpoint:
                    validate_oauth_endpoint(auth_endpoint)
                    validate_oauth_endpoint(token_endpoint)
                    self.logger.info(
                        "Using configured OAuth endpoints",
                        provider=self.id,
                    )
                    return OAuthDiscoveryResult(
                        authorization_endpoint=auth_endpoint,
                        token_endpoint=token_endpoint,
                        token_methods=[],
                        registration_endpoint=None,
                    )

            self.logger.error(
                "Failed to discover OAuth endpoints",
                provider=self.id,
                error=str(e),
            )
            raise ValueError(
                f"Could not discover OAuth endpoints from {discovery_url} "
                f"and no fallback endpoints provided"
            ) from e

    @classmethod
    async def _discover_oauth_endpoints_async(
        cls,
        logger_instance,
        *,
        discovered_auth_endpoint: str | None = None,
        discovered_token_endpoint: str | None = None,
    ) -> OAuthDiscoveryResult:
        """Async discovery counterpart used in event-loop contexts."""

        base_url = cls._get_base_url()
        discovery_url = oauth_authorization_server_metadata_urls(base_url)[0]

        try:
            await validate_oauth_endpoint_resolves_public_async(discovery_url)
            async with httpx.AsyncClient() as client:
                response = await client.get(discovery_url, timeout=10.0)
            response.raise_for_status()
            metadata = OAuthServerMetadata.from_json(response.json())
            if metadata is None or not metadata.is_complete:
                raise ValueError("OAuth discovery document is missing endpoints")
            authorization_endpoint = metadata.authorization_endpoint
            token_endpoint = metadata.token_endpoint
            if authorization_endpoint is None or token_endpoint is None:
                raise ValueError("OAuth discovery document is missing endpoints")
            token_methods = metadata.token_endpoint_auth_methods_supported
            registration_endpoint = metadata.registration_endpoint

            # Validate discovered endpoints for security
            base_domain = urlparse(base_url).hostname
            cls._validate_discovered_oauth_endpoint(authorization_endpoint, base_domain)
            cls._validate_discovered_oauth_endpoint(token_endpoint, base_domain)
            if registration_endpoint:
                cls._validate_discovered_oauth_endpoint(
                    registration_endpoint, base_domain
                )

            logger_instance.info(
                "Discovered OAuth endpoints",
                provider=cls.id,
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
                )
                return OAuthDiscoveryResult(
                    authorization_endpoint=cls._fallback_auth_endpoint,
                    token_endpoint=cls._fallback_token_endpoint,
                    token_methods=token_methods,
                    registration_endpoint=None,
                )

            # If _fallback_token_endpoint is None, check if configured endpoints are available
            if cls._fallback_token_endpoint is None:
                # Check for configured endpoints from init params or class defaults
                auth_endpoint = (
                    discovered_auth_endpoint
                    or cls._fallback_auth_endpoint
                    or getattr(cls, "default_authorization_endpoint", None)
                )
                token_endpoint = discovered_token_endpoint or getattr(
                    cls, "default_token_endpoint", None
                )

                if auth_endpoint and token_endpoint:
                    validate_oauth_endpoint(auth_endpoint)
                    validate_oauth_endpoint(token_endpoint)
                    logger_instance.info(
                        "Using configured OAuth endpoints",
                        provider=cls.id,
                    )
                    return OAuthDiscoveryResult(
                        authorization_endpoint=auth_endpoint,
                        token_endpoint=token_endpoint,
                        token_methods=[],
                        registration_endpoint=None,
                    )

            logger_instance.error(
                "Failed to discover OAuth endpoints",
                provider=cls.id,
                error=str(e),
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

        logger_instance.info(
            "Attempting dynamic client registration",
            provider=cls.id,
        )

        try:
            registration_response = await cls._submit_registration_request(
                registration_endpoint, registration_payload
            )
        except httpx.HTTPError as exc:
            logger_instance.error(
                "Dynamic registration failed",
                provider=cls.id,
                error=str(exc),
            )
            raise
        except ValueError as exc:
            logger_instance.error(
                "Dynamic registration error",
                provider=cls.id,
                error=str(exc),
            )
            raise

        auth_method = (
            registration_response.token_endpoint_auth_method or registration_auth_method
        )

        logger_instance.info(
            "Registered OAuth client dynamically",
            provider=cls.id,
        )

        return DynamicRegistrationResult(
            client_id=registration_response.client_id,
            client_secret=registration_response.client_secret,
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

        The resource parameter identifies the MCP server that the token will be used
        with. Use the most specific canonical MCP URI so authorization and token
        requests match strict MCP resource/audience checks.
        """
        params = super()._get_additional_authorize_params()
        params["resource"] = self._get_resource_uri()
        return params

    @classmethod
    async def instantiate(
        cls, *, config: ProviderConfig | None = None, **kwargs: Any
    ) -> Self:
        """Instantiate MCP providers without blocking the event loop."""

        logger_instance = logger.bind(service=f"{cls.__name__}")

        # Extract discovered endpoints from kwargs if provided
        # These will be used as fallbacks if discovery fails
        discovered_auth_endpoint = kwargs.pop("authorization_endpoint", None)
        discovered_token_endpoint = kwargs.pop("token_endpoint", None)

        discovery_result = await cls._discover_oauth_endpoints_async(
            logger_instance,
            discovered_auth_endpoint=discovered_auth_endpoint,
            discovered_token_endpoint=discovered_token_endpoint,
        )

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
            token_methods=discovery_result.token_methods,
        )

        provider = cls(**init_kwargs)

        if registration_auth_method:
            provider._client_registration_auth_method = registration_auth_method

        return provider

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Add MCP-specific token exchange parameters.

        The resource parameter must be included in token requests per MCP spec,
        and must match the value used in the authorization request.
        """
        params = super()._get_additional_token_params()
        params["resource"] = self._get_resource_uri()
        return params
