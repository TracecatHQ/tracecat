"""Service for managing user integrations with external services."""

import re
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
import orjson
import sqlalchemy as sa
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from pydantic import SecretStr
from slugify import slugify
from sqlalchemy import and_, delete, or_, select, update

from tracecat import config
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.authz.controls import require_scope
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    AgentPreset,
    AgentSession,
    MCPIntegration,
    OAuthIntegration,
    OAuthStateDB,
    WorkspaceOAuthProvider,
)
from tracecat.identifiers import UserID
from tracecat.integrations.catalog.loader import (
    get_platform_mcp_catalog_entries,
    get_platform_mcp_catalog_entry_by_provider_id,
    get_platform_mcp_catalog_entry_by_slug,
)
from tracecat.integrations.catalog.types import PlatformMCPCatalogEntry
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
from tracecat.integrations.mcp_validation import (
    ALLOWED_MCP_COMMANDS,
    MAX_SERVER_NAME_LENGTH,
    MCPValidationError,
    validate_mcp_command_config,
)
from tracecat.integrations.providers import get_provider_class
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    BaseOAuthProvider,
    ClientCredentialsOAuthProvider,
    CustomOAuthProviderMixin,
    MCPAuthProvider,
    oauth_authorization_server_metadata_urls,
    validate_oauth_endpoint,
    validate_oauth_endpoint_resolves_public_async,
)
from tracecat.integrations.schemas import (
    CustomOAuthProviderCreate,
    IntegrationOAuthConnect,
    MCPConnectionSpec,
    MCPHttpIntegrationCreate,
    MCPHTTPOAuth2ConnectionSpec,
    MCPIntegrationCreate,
    MCPIntegrationSource,
    MCPIntegrationUpdate,
    MCPStdioIntegrationCreate,
    PlatformMCPCatalogState,
    ProviderConfig,
    ProviderKey,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.types import (
    DCRResponse,
    MCPServerType,
    OAuthServerMetadata,
    TokenResponse,
)
from tracecat.secrets.encryption import decrypt_value, encrypt_value, is_set
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.enums import Entitlement


@dataclass(frozen=True)
class PlatformMCPCatalogConnectResult:
    """Result of connecting a platform MCP catalog entry."""

    mcp_integration: MCPIntegration | None = None
    oauth_connect: IntegrationOAuthConnect | None = None


@dataclass(frozen=True)
class MCPIntegrationWithState:
    """Workspace MCP row with computed connection state."""

    integration: MCPIntegration
    state: PlatformMCPCatalogState


class InsecureOAuthEndpointError(ValueError):
    """Raised when OAuth endpoints are not secured with HTTPS."""


class ProviderConfigurationRequiredError(ValueError):
    """Raised when an OAuth provider must be configured before connection."""


@dataclass(frozen=True)
class MCPOAuthDiscoveryEndpoints:
    """Discovered OAuth metadata for a generic MCP resource server."""

    authorization_endpoint: str
    token_endpoint: str
    token_methods: list[str]
    registration_endpoint: str | None
    resource: str


@dataclass(frozen=True)
class MCPOAuthRegistrationResult:
    """Dynamic client registration result for a generic MCP resource server."""

    client_id: str
    client_secret: str | None
    auth_method: str | None


@dataclass(frozen=True)
class MCPOAuthCallbackState:
    """Custom MCP OAuth callback data stored in the short-lived state row."""

    code_verifier: str | None
    token_auth_method: str | None


_CUSTOM_MCP_OAUTH_PROVIDER_PREFIX = "custom_mcp_"
_MCP_TOKEN_AUTH_METHODS: frozenset[str] = frozenset(
    {"client_secret_basic", "client_secret_post", "none"}
)
_CATALOG_PLACEHOLDER_RE = re.compile(
    r"(\{[A-Za-z_][A-Za-z0-9_]*\}|<[A-Za-z_][A-Za-z0-9_-]*>)"
)


class IntegrationService(BaseWorkspaceService):
    """Service for managing user integrations."""

    service_name = "integrations"

    @staticmethod
    def _escape_like_pattern(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

    @staticmethod
    def _validate_https_endpoint(
        endpoint: str | None, *, field_name: str
    ) -> str | None:
        """Ensure OAuth endpoints use HTTPS before persistence or use."""
        if endpoint is None:
            return None
        parsed = urlparse(endpoint)
        if parsed.scheme.lower() != "https":
            raise InsecureOAuthEndpointError(f"{field_name} must use HTTPS: {endpoint}")
        if not parsed.netloc:
            raise InsecureOAuthEndpointError(
                f"{field_name} must include a hostname: {endpoint}"
            )
        return endpoint

    @staticmethod
    def _normalize_scopes(scopes: list[str] | None) -> list[str]:
        """Normalize scopes by trimming whitespace and removing duplicates."""
        if not scopes:
            return []
        normalized: list[str] = []
        for scope in scopes:
            value = scope.strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _validate_stdio_server_config(
        *,
        command: str | None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Validate stdio server command configuration before persistence."""
        normalized_command = command.strip() if command else ""
        if not normalized_command:
            raise ValueError("stdio_command is required for stdio-type servers")
        try:
            validate_mcp_command_config(
                command=normalized_command,
                args=args,
                env=env,
            )
        except MCPValidationError as exc:
            raise ValueError(str(exc)) from exc

    async def _provider_identifier_taken(
        self, provider_id: str, grant_type: OAuthGrantType
    ) -> bool:
        """Check whether a provider identifier conflicts with existing providers."""
        if get_provider_class(ProviderKey(id=provider_id, grant_type=grant_type)):
            return True

        statement = select(WorkspaceOAuthProvider).where(
            WorkspaceOAuthProvider.workspace_id == self.workspace_id,
            WorkspaceOAuthProvider.provider_id == provider_id,
            WorkspaceOAuthProvider.grant_type == grant_type,
        )
        result = await self.session.execute(statement)
        return result.scalars().first() is not None

    async def _generate_custom_provider_id(
        self,
        *,
        name: str,
        requested_id: str | None,
        grant_type: OAuthGrantType,
        allow_reserved_id: bool = False,
    ) -> str:
        """Generate a unique provider identifier for a custom provider."""
        base_source = requested_id or name
        slug = slugify(base_source, separator="_") or uuid4().hex
        if not slug.startswith("custom_"):
            slug = f"custom_{slug}"
        if not allow_reserved_id and slug.startswith(_CUSTOM_MCP_OAUTH_PROVIDER_PREFIX):
            # The MCP OAuth discovery pipeline owns the ``custom_mcp_`` id
            # namespace (callback, refresh, and delete branch on it), so a
            # regular custom provider must never land in it.
            if requested_id is not None:
                raise ValueError(
                    f"Provider IDs starting with "
                    f"'{_CUSTOM_MCP_OAUTH_PROVIDER_PREFIX}' are reserved"
                )
            slug = f"custom_oauth_{slug.removeprefix('custom_')}"

        candidate = slug
        suffix = 1
        while await self._provider_identifier_taken(candidate, grant_type):
            candidate = f"{slug}_{suffix}"
            suffix += 1
        return candidate

    async def list_custom_providers(self) -> Sequence[WorkspaceOAuthProvider]:
        """List all custom OAuth providers for the current workspace."""
        statement = select(WorkspaceOAuthProvider).where(
            WorkspaceOAuthProvider.workspace_id == self.workspace_id
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_custom_provider(
        self, *, provider_key: ProviderKey
    ) -> WorkspaceOAuthProvider | None:
        """Fetch a custom provider definition for the workspace."""
        statement = select(WorkspaceOAuthProvider).where(
            WorkspaceOAuthProvider.workspace_id == self.workspace_id,
            WorkspaceOAuthProvider.provider_id == provider_key.id,
            WorkspaceOAuthProvider.grant_type == provider_key.grant_type,
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    @staticmethod
    def _build_custom_provider_class(
        provider: WorkspaceOAuthProvider,
    ) -> type[BaseOAuthProvider]:
        """Construct a dynamic provider class for a custom provider definition."""
        base_cls: type[BaseOAuthProvider]
        if provider.grant_type == OAuthGrantType.AUTHORIZATION_CODE:
            base_cls = AuthorizationCodeOAuthProvider
        else:
            base_cls = ClientCredentialsOAuthProvider

        metadata = ProviderMetadata(
            id=provider.provider_id,
            name=provider.name,
            description=provider.description
            or f"Custom provider {provider.provider_id}",
            logo_url=None,
            setup_instructions=None,
            requires_config=True,
            enabled=True,
            api_docs_url=None,
            setup_guide_url=None,
            troubleshooting_url=None,
        )

        provider_scopes = ProviderScopes(default=list(provider.scopes or []))

        attrs = {
            "__module__": __name__,
            "id": provider.provider_id,
            "metadata": metadata,
            "scopes": provider_scopes,
            "default_authorization_endpoint": provider.authorization_endpoint,
            "default_token_endpoint": provider.token_endpoint,
            "authorization_endpoint_help": None,
            "token_endpoint_help": None,
        }

        class_name = f"CustomProvider_{provider.id.hex}"
        return cast(
            type[BaseOAuthProvider],
            type(class_name, (CustomOAuthProviderMixin, base_cls), attrs),
        )

    async def resolve_provider_impl(
        self, *, provider_key: ProviderKey
    ) -> type[BaseOAuthProvider] | None:
        """Resolve a provider implementation from registry or workspace custom providers."""
        provider_impl = get_provider_class(provider_key)
        if provider_impl is not None:
            return provider_impl

        custom_provider = await self.get_custom_provider(provider_key=provider_key)
        if custom_provider is None:
            return None
        return self._build_custom_provider_class(custom_provider)

    @require_scope("integration:create")
    async def create_custom_provider(
        self,
        *,
        params: CustomOAuthProviderCreate,
        allow_reserved_id: bool = False,
    ) -> WorkspaceOAuthProvider:
        """Create a new custom OAuth provider for the workspace."""
        provider_id = await self._generate_custom_provider_id(
            name=params.name,
            requested_id=params.provider_id,
            grant_type=params.grant_type,
            allow_reserved_id=allow_reserved_id,
        )
        authorization_endpoint = self._validate_https_endpoint(
            params.authorization_endpoint, field_name="authorization_endpoint"
        )
        token_endpoint = self._validate_https_endpoint(
            params.token_endpoint, field_name="token_endpoint"
        )
        scopes = self._normalize_scopes(params.scopes)

        provider = WorkspaceOAuthProvider(
            workspace_id=self.workspace_id,
            provider_id=provider_id,
            name=params.name.strip(),
            description=params.description,
            grant_type=params.grant_type,
            authorization_endpoint=authorization_endpoint
            or params.authorization_endpoint,
            token_endpoint=token_endpoint or params.token_endpoint,
            scopes=scopes,
        )

        self.session.add(provider)
        await self.session.commit()
        await self.session.refresh(provider)

        await self.store_provider_config(
            provider_key=ProviderKey(id=provider_id, grant_type=params.grant_type),
            client_id=params.client_id,
            client_secret=params.client_secret,
            authorization_endpoint=provider.authorization_endpoint,
            token_endpoint=provider.token_endpoint,
            requested_scopes=scopes,
        )

        self.logger.info(
            "Created custom OAuth provider",
            provider_id=provider_id,
            grant_type=params.grant_type,
        )

        return provider

    @require_scope("integration:delete")
    async def delete_custom_provider(self, *, provider_key: ProviderKey) -> bool:
        """Delete a custom OAuth provider definition."""
        custom_provider = await self.get_custom_provider(provider_key=provider_key)
        if custom_provider is None:
            return False

        await self.session.delete(custom_provider)
        await self.session.commit()

        self.logger.info(
            "Deleted custom OAuth provider",
            provider_id=provider_key.id,
            grant_type=provider_key.grant_type,
            workspace_id=self.workspace_id,
        )
        return True

    _encryption_key: str

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._encryption_key = get_db_encryption_key()

    async def get_integration(
        self,
        *,
        provider_key: ProviderKey,
        user_id: UserID | None = None,
    ) -> OAuthIntegration | None:
        """Get a user's integration for a specific provider."""

        statement = select(OAuthIntegration).where(
            OAuthIntegration.workspace_id == self.workspace_id,
            OAuthIntegration.provider_id == provider_key.id,
            OAuthIntegration.grant_type == provider_key.grant_type,
        )
        if user_id is not None:
            statement = statement.where(OAuthIntegration.user_id == user_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_integrations(
        self, *, provider_keys: set[ProviderKey] | None = None
    ) -> Sequence[OAuthIntegration]:
        """List all integrations for a workspace, optionally filtered by providers."""
        statement = select(OAuthIntegration).where(
            OAuthIntegration.workspace_id == self.workspace_id
        )
        if provider_keys:
            # Create conditions for each provider (provider_id + grant_type combination)
            provider_conditions = [
                and_(
                    OAuthIntegration.provider_id == provider.id,
                    OAuthIntegration.grant_type == provider.grant_type,
                )
                for provider in provider_keys
            ]
            statement = statement.where(or_(*provider_conditions))
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("integration:create", "integration:update", require_all=False)
    async def start_authorization_code_connect(
        self,
        *,
        provider_key: ProviderKey,
        provider_impl: type[AuthorizationCodeOAuthProvider],
    ) -> IntegrationOAuthConnect:
        """Initiate an authorization-code OAuth connection for a provider."""
        if self.workspace_id is None or self.role.user_id is None:
            raise ValueError("Workspace and user ID is required")

        integration = await self.get_integration(provider_key=provider_key)
        provider_config = (
            self.get_provider_config(
                integration=integration,
                provider_impl=provider_impl,
                default_scopes=provider_impl.scopes.default,
            )
            if integration
            else None
        )

        if provider_impl.metadata.requires_config:
            if integration is None or provider_config is None:
                raise ProviderConfigurationRequiredError(
                    "Provider is not configured for this workspace"
                )
            provider = await provider_impl.instantiate(config=provider_config)
        else:
            provider = await provider_impl.instantiate(config=provider_config)
            if (integration is None or provider_config is None) and provider.client_id:
                await self.store_provider_config(
                    provider_key=provider_key,
                    client_id=provider.client_id,
                    client_secret=SecretStr(provider.client_secret)
                    if provider.client_secret
                    else None,
                    authorization_endpoint=provider.authorization_endpoint,
                    token_endpoint=provider.token_endpoint,
                    requested_scopes=provider.requested_scopes,
                )

        # Clean up expired state entries globally before creating a new one.
        async with get_async_session_bypass_rls_context_manager() as bypass_session:
            await bypass_session.execute(
                delete(OAuthStateDB).where(OAuthStateDB.expires_at < datetime.now(UTC))
            )
            await bypass_session.commit()

        state_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        oauth_state = OAuthStateDB(
            state=state_id,
            workspace_id=self.workspace_id,
            user_id=self.role.user_id,
            provider_id=provider_key.id,
            expires_at=expires_at,
        )
        self.session.add(oauth_state)
        await self.session.commit()

        auth_url, code_verifier = await provider.get_authorization_url(str(state_id))
        if code_verifier:
            oauth_state.code_verifier = code_verifier
            await self.session.commit()

        self.logger.info(
            "Generated authorization URL",
            provider=provider.id,
            has_code_verifier=code_verifier is not None,
        )
        return IntegrationOAuthConnect(auth_url=auth_url, provider_id=provider.id)

    # ------------------------------------------------------------------------
    # Generic MCP OAuth (BYO / catalog) with dynamic client registration.
    #
    # Flow, top to bottom: discover endpoints from the server's .well-known
    # metadata -> dynamically register a client (DCR) -> create a custom OAuth
    # provider -> start the authorization redirect -> complete the callback ->
    # refresh tokens. The private helpers above each public method support that
    # pipeline; "custom_mcp_" provider IDs identify integrations it owns.
    # ------------------------------------------------------------------------

    @staticmethod
    def _is_custom_mcp_oauth_provider(provider_id: str) -> bool:
        return provider_id.startswith(_CUSTOM_MCP_OAUTH_PROVIDER_PREFIX)

    @staticmethod
    def _mcp_resource_base_url(server_uri: str) -> str:
        parsed = urlparse(server_uri)
        if parsed.scheme.lower() != "https":
            raise ValueError("MCP OAuth discovery requires an HTTPS server URI")
        if not parsed.netloc:
            raise ValueError("MCP server URI is missing a hostname")
        return f"https://{parsed.netloc}"

    @staticmethod
    def _mcp_resource_uri(server_uri: str) -> str:
        parsed = urlparse(server_uri.strip())
        if parsed.scheme.lower() != "https":
            raise ValueError("MCP OAuth discovery requires an HTTPS server URI")
        if not parsed.hostname:
            raise ValueError("MCP server URI is missing a hostname")
        if parsed.fragment:
            raise ValueError("MCP OAuth resource URI cannot include a fragment")

        host = parsed.hostname.lower()
        # ``urlparse`` strips the brackets from IPv6 literals; restore them so
        # the rebuilt netloc stays a valid authority (e.g. ``[::1]:443``).
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        path = parsed.path if parsed.path else ""
        return urlunparse(("https", netloc, path, "", parsed.query, ""))

    @staticmethod
    def _mcp_oauth_metadata_urls(server_uri: str) -> list[str]:
        parsed = urlparse(server_uri)
        base_url = IntegrationService._mcp_resource_base_url(server_uri)
        urls: list[str] = []
        if parsed.path and parsed.path != "/":
            urls.append(f"{base_url}/.well-known/oauth-protected-resource{parsed.path}")
        urls.append(f"{base_url}/.well-known/oauth-protected-resource")
        urls.append(f"{base_url}/.well-known/oauth-authorization-server")
        return urls

    @staticmethod
    def _validate_mcp_oauth_endpoint(
        endpoint: str,
        *,
        base_domain: str | None = None,
        allowed_hosts: frozenset[str] = frozenset(),
    ) -> str:
        """Validate a generic MCP OAuth endpoint discovered from metadata.

        Generic BYO/catalog DCR follows the trust chain from the user-supplied
        MCP server URI to protected-resource or authorization-server metadata.
        Endpoint hosts must match the metadata host that advertised them,
        except for exact hosts of OAuth endpoints pinned on the repo-owned
        catalog row (still SSRF-validated).
        """
        hostname = urlparse(endpoint).hostname
        if not hostname:
            raise InsecureOAuthEndpointError(
                f"oauth_endpoint must include a hostname: {endpoint}"
            )
        if hostname in allowed_hosts:
            validate_oauth_endpoint(endpoint)
            return endpoint
        validate_oauth_endpoint(endpoint, base_domain=base_domain)
        return endpoint

    @staticmethod
    def _select_mcp_registration_auth_method(methods: list[str]) -> str | None:
        if "client_secret_post" in methods:
            return "client_secret_post"
        if "client_secret_basic" in methods:
            return "client_secret_basic"
        if "none" in methods:
            return "none"
        return None

    @staticmethod
    def _select_mcp_token_auth_method(
        *, methods: list[str], client_secret: str | None
    ) -> str | None:
        if client_secret:
            for method in ("client_secret_post", "client_secret_basic"):
                if method in methods:
                    return method
            return "client_secret_basic"
        if "none" in methods:
            return "none"
        return None

    @staticmethod
    def _normalize_mcp_token_auth_method(method: str | None) -> str | None:
        if method in _MCP_TOKEN_AUTH_METHODS:
            return method
        return None

    @classmethod
    def _mcp_token_auth_method(
        cls,
        *,
        methods: list[str],
        client_secret: str | None,
        registered_auth_method: str | None = None,
    ) -> str | None:
        return cls._normalize_mcp_token_auth_method(
            registered_auth_method
        ) or cls._select_mcp_token_auth_method(
            methods=methods,
            client_secret=client_secret,
        )

    @classmethod
    def _encode_mcp_oauth_callback_state(
        cls,
        *,
        code_verifier: str,
        token_auth_method: str | None,
    ) -> str:
        token_auth_method = cls._normalize_mcp_token_auth_method(token_auth_method)
        if token_auth_method is None:
            return code_verifier
        return orjson.dumps(
            {
                "code_verifier": code_verifier,
                "token_endpoint_auth_method": token_auth_method,
            }
        ).decode("utf-8")

    @classmethod
    def _decode_mcp_oauth_callback_state(
        cls,
        value: str | None,
    ) -> MCPOAuthCallbackState:
        if not value:
            return MCPOAuthCallbackState(code_verifier=None, token_auth_method=None)
        if not value.lstrip().startswith("{"):
            return MCPOAuthCallbackState(code_verifier=value, token_auth_method=None)
        try:
            parsed = orjson.loads(value)
        except orjson.JSONDecodeError:
            return MCPOAuthCallbackState(code_verifier=value, token_auth_method=None)
        if not isinstance(parsed, dict):
            return MCPOAuthCallbackState(code_verifier=value, token_auth_method=None)

        raw_code_verifier = parsed.get("code_verifier")
        code_verifier = (
            raw_code_verifier
            if isinstance(raw_code_verifier, str) and raw_code_verifier
            else None
        )
        raw_auth_method = parsed.get("token_endpoint_auth_method")
        token_auth_method = cls._normalize_mcp_token_auth_method(
            raw_auth_method if isinstance(raw_auth_method, str) else None
        )
        return MCPOAuthCallbackState(
            code_verifier=code_verifier,
            token_auth_method=token_auth_method,
        )

    @staticmethod
    def _mcp_oauth_redirect_uri() -> str:
        """The single OAuth callback URL all MCP discovery flows redirect to.

        Read from config at call time (not import time) so test/env overrides of
        ``TRACECAT__PUBLIC_APP_URL`` take effect.
        """
        return f"{config.TRACECAT__PUBLIC_APP_URL}/integrations/callback"

    @classmethod
    def _build_mcp_oauth_client(
        cls,
        *,
        client_id: str,
        client_secret: str | None,
        token_auth_method: str | None,
        with_redirect: bool = True,
        with_response_type: bool = False,
    ) -> AsyncOAuth2Client:
        """Construct the authlib client shared by MCP authorize/callback/refresh.

        The three flows differ only in a couple of kwargs: authorization needs a
        ``response_type``, refresh needs no redirect URI, and the token endpoint
        auth method is only set when discovery advertised one.
        """
        client_kwargs: dict[str, Any] = {
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if with_redirect:
            # Authorize/callback are PKCE flows; refresh carries neither.
            client_kwargs["redirect_uri"] = cls._mcp_oauth_redirect_uri()
            client_kwargs["code_challenge_method"] = "S256"
        if with_response_type:
            client_kwargs["response_type"] = "code"
        if token_auth_method:
            client_kwargs["token_endpoint_auth_method"] = token_auth_method
        return AsyncOAuth2Client(**client_kwargs)

    async def _fetch_oauth_json(self, url: str) -> OAuthServerMetadata | None:
        await validate_oauth_endpoint_resolves_public_async(url)
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return OAuthServerMetadata.from_json(response.json())

    async def _discover_mcp_oauth_endpoints(
        self,
        *,
        server_uri: str,
        allowed_endpoint_hosts: frozenset[str] = frozenset(),
    ) -> MCPOAuthDiscoveryEndpoints:
        resource_uri = self._mcp_resource_uri(server_uri)
        resource_host = urlparse(resource_uri).hostname
        if resource_host is None:
            raise ValueError("MCP server URI is missing a hostname")

        # Two-tier OAuth discovery (RFC 9728 + RFC 8414): first try the server's
        # own .well-known metadata. If it advertises the endpoints directly we use
        # them; otherwise it points at separate authorization servers whose
        # .well-known metadata we fetch in the second pass below.
        auth_server_metadata_urls: list[str] = []
        direct_metadata: OAuthServerMetadata | None = None
        direct_metadata_host: str | None = None
        for metadata_url in self._mcp_oauth_metadata_urls(server_uri):
            metadata = await self._fetch_oauth_json(metadata_url)
            if not metadata:
                continue
            # Metadata may override the canonical resource identifier we send as
            # the OAuth `resource` parameter; re-validate it before trusting it.
            if metadata.resource:
                resource_uri = self._mcp_resource_uri(metadata.resource)
            if metadata.is_complete:
                direct_metadata = metadata
                direct_metadata_host = urlparse(metadata_url).hostname
                break
            for issuer in metadata.authorization_servers:
                auth_server_metadata_urls.extend(
                    oauth_authorization_server_metadata_urls(issuer)
                )

        if direct_metadata is None:
            for metadata_url in auth_server_metadata_urls:
                metadata = await self._fetch_oauth_json(metadata_url)
                if metadata and metadata.is_complete:
                    direct_metadata = metadata
                    direct_metadata_host = urlparse(metadata_url).hostname
                    break

        if direct_metadata is None:
            raise ValueError(f"Could not discover OAuth endpoints from {server_uri}")

        authorization_endpoint = direct_metadata.authorization_endpoint
        token_endpoint = direct_metadata.token_endpoint
        if authorization_endpoint is None or token_endpoint is None:
            raise ValueError(f"OAuth metadata from {server_uri} is missing endpoints")

        token_methods = direct_metadata.token_endpoint_auth_methods_supported
        registration_endpoint = direct_metadata.registration_endpoint

        return MCPOAuthDiscoveryEndpoints(
            authorization_endpoint=self._validate_mcp_oauth_endpoint(
                authorization_endpoint,
                base_domain=direct_metadata_host,
                allowed_hosts=allowed_endpoint_hosts,
            ),
            token_endpoint=self._validate_mcp_oauth_endpoint(
                token_endpoint,
                base_domain=direct_metadata_host,
                allowed_hosts=allowed_endpoint_hosts,
            ),
            token_methods=token_methods,
            registration_endpoint=self._validate_mcp_oauth_endpoint(
                registration_endpoint,
                base_domain=direct_metadata_host,
                allowed_hosts=allowed_endpoint_hosts,
            )
            if registration_endpoint
            else None,
            resource=resource_uri,
        )

    async def _resolve_mcp_oauth_endpoints(
        self,
        *,
        server_uri: str,
        provider_config: ProviderConfig,
    ) -> MCPOAuthDiscoveryEndpoints:
        """Resolve OAuth endpoints for a custom MCP provider.

        Catalog rows that supply static authorization/token endpoints persist
        them on the ``custom_mcp_*`` provider config. Those providers may not
        advertise MCP OAuth discovery at all, or advertise endpoints on a
        different host, so prefer the stored endpoints and only fall back to
        discovery when the provider config does not carry both. The stored
        endpoints are still validated against SSRF before use.
        """

        if provider_config.authorization_endpoint and provider_config.token_endpoint:
            for endpoint in (
                provider_config.authorization_endpoint,
                provider_config.token_endpoint,
            ):
                validate_oauth_endpoint(endpoint)
            return MCPOAuthDiscoveryEndpoints(
                authorization_endpoint=provider_config.authorization_endpoint,
                token_endpoint=provider_config.token_endpoint,
                # The token auth method is selected from client_secret presence
                # when no discovered methods are available, so an empty list is
                # safe here.
                token_methods=[],
                registration_endpoint=None,
                resource=self._mcp_resource_uri(server_uri),
            )
        return await self._discover_mcp_oauth_endpoints(server_uri=server_uri)

    async def _perform_mcp_dynamic_registration(
        self,
        *,
        registration_endpoint: str,
        client_name: str,
        token_auth_method: str | None,
    ) -> MCPOAuthRegistrationResult:
        payload: dict[str, object] = {
            "client_name": client_name,
            "redirect_uris": [self._mcp_oauth_redirect_uri()],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        }
        if token_auth_method:
            payload["token_endpoint_auth_method"] = token_auth_method

        await validate_oauth_endpoint_resolves_public_async(registration_endpoint)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                registration_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
        response.raise_for_status()
        try:
            registration_response = DCRResponse.model_validate(response.json())
        except ValueError as exc:
            raise ValueError(
                "Dynamic client registration did not return client_id"
            ) from exc
        auth_method = (
            registration_response.token_endpoint_auth_method or token_auth_method
        )
        return MCPOAuthRegistrationResult(
            client_id=registration_response.client_id,
            client_secret=registration_response.client_secret,
            auth_method=auth_method,
        )

    async def _generate_custom_mcp_provider_id(self, *, name: str) -> str:
        base = slugify(name, separator="_") or uuid4().hex
        base = f"{_CUSTOM_MCP_OAUTH_PROVIDER_PREFIX}{base}"
        candidate = base
        suffix = 1
        while await self._provider_identifier_taken(
            candidate, OAuthGrantType.AUTHORIZATION_CODE
        ):
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    async def _create_custom_mcp_oauth_provider(
        self,
        *,
        name: str,
        description: str | None,
        endpoints: MCPOAuthDiscoveryEndpoints,
        registration: MCPOAuthRegistrationResult,
        scopes: list[str] | None,
    ) -> OAuthIntegration:
        provider_id = await self._generate_custom_mcp_provider_id(name=name)
        await self.create_custom_provider(
            allow_reserved_id=True,
            params=CustomOAuthProviderCreate(
                provider_id=provider_id,
                name=name,
                description=description,
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                authorization_endpoint=endpoints.authorization_endpoint,
                token_endpoint=endpoints.token_endpoint,
                scopes=scopes,
                client_id=registration.client_id,
                client_secret=SecretStr(registration.client_secret)
                if registration.client_secret
                else None,
            ),
        )
        provider_key = ProviderKey(
            id=provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        integration = await self.get_integration(provider_key=provider_key)
        if integration is None:
            raise ValueError("Failed to create MCP OAuth integration")
        return integration

    async def _start_custom_mcp_oauth_authorization(
        self,
        *,
        integration: OAuthIntegration,
        server_uri: str,
        endpoints: MCPOAuthDiscoveryEndpoints,
        registration: MCPOAuthRegistrationResult,
    ) -> IntegrationOAuthConnect:
        if self.role.user_id is None:
            raise ValueError("User ID is required")

        async with get_async_session_bypass_rls_context_manager() as bypass_session:
            await bypass_session.execute(
                delete(OAuthStateDB).where(OAuthStateDB.expires_at < datetime.now(UTC))
            )
            await bypass_session.commit()

        state_id = uuid.uuid4()
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = create_s256_code_challenge(code_verifier)
        token_auth_method = self._mcp_token_auth_method(
            methods=endpoints.token_methods,
            client_secret=registration.client_secret,
            registered_auth_method=registration.auth_method,
        )
        oauth_state = OAuthStateDB(
            state=state_id,
            workspace_id=self.workspace_id,
            user_id=self.role.user_id,
            provider_id=integration.provider_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            code_verifier=self._encode_mcp_oauth_callback_state(
                code_verifier=code_verifier,
                token_auth_method=token_auth_method,
            ),
        )
        self.session.add(oauth_state)
        await self.session.commit()

        client = self._build_mcp_oauth_client(
            client_id=registration.client_id,
            client_secret=registration.client_secret,
            token_auth_method=token_auth_method,
            with_response_type=True,
        )
        auth_url, _ = client.create_authorization_url(
            endpoints.authorization_endpoint,
            state=str(state_id),
            code_challenge=code_challenge,
            code_challenge_method="S256",
            resource=endpoints.resource,
        )
        return IntegrationOAuthConnect(
            auth_url=auth_url,
            provider_id=integration.provider_id,
        )

    @require_scope("integration:create")
    async def connect_mcp_oauth_discovery(
        self,
        *,
        params: MCPHttpIntegrationCreate,
        catalog_spec: MCPConnectionSpec | None = None,
        existing_mcp_integration: MCPIntegration | None = None,
    ) -> PlatformMCPCatalogConnectResult:
        if params.server_type != "http" or params.auth_type != MCPAuthType.OAUTH2:
            raise ValueError("MCP OAuth discovery requires an HTTP OAuth MCP server")
        if params.oauth_integration_id is not None:
            return PlatformMCPCatalogConnectResult(
                mcp_integration=await self.create_mcp_integration(params=params)
            )

        scopes: list[str] | None = None
        if catalog_spec is None and params.catalog_slug:
            catalog = get_platform_mcp_catalog_entry_by_slug(
                params.catalog_slug, include_private=True
            )
            if catalog is not None:
                catalog_spec = self._catalog_connection_spec(catalog)
        allowed_endpoint_hosts: frozenset[str] = frozenset()
        if catalog_spec is not None:
            if not isinstance(catalog_spec, MCPHTTPOAuth2ConnectionSpec):
                raise ValueError("Catalog option is not an HTTP OAuth MCP server")
            scopes = catalog_spec.scopes
            # Hosts of catalog-pinned OAuth endpoints are trusted during
            # discovery; the catalog is repo-owned, so a pinned endpoint
            # states explicitly where the provider serves OAuth.
            allowed_endpoint_hosts = frozenset(
                hostname
                for endpoint in (
                    catalog_spec.oauth_authorization_endpoint,
                    catalog_spec.oauth_token_endpoint,
                )
                if endpoint and (hostname := urlparse(endpoint).hostname)
            )

        endpoints = await self._discover_mcp_oauth_endpoints(
            server_uri=params.server_uri,
            allowed_endpoint_hosts=allowed_endpoint_hosts,
        )
        # Prefer user-supplied OAuth client credentials; otherwise fall back to
        # dynamic client registration (DCR) against the discovered endpoint.
        registration = self._mcp_oauth_client_registration_from_credentials(
            params=params,
            catalog_spec=catalog_spec,
        )
        used_byo_credentials = registration is not None
        if registration is None:
            if not endpoints.registration_endpoint:
                raise ValueError(
                    "MCP OAuth server does not advertise dynamic registration"
                )
            registration = await self._perform_mcp_dynamic_registration(
                registration_endpoint=endpoints.registration_endpoint,
                client_name=params.name,
                token_auth_method=self._select_mcp_registration_auth_method(
                    endpoints.token_methods
                ),
            )
        oauth_integration = await self._create_custom_mcp_oauth_provider(
            name=params.name,
            description=params.description,
            endpoints=endpoints,
            registration=registration,
            scopes=scopes,
        )
        if existing_mcp_integration is not None:
            existing_mcp_integration.oauth_integration_id = oauth_integration.id
            existing_mcp_integration.auth_type = MCPAuthType.OAUTH2
            self.session.add(existing_mcp_integration)
            await self.session.commit()
            await self.session.refresh(existing_mcp_integration)
            mcp_integration = existing_mcp_integration
        else:
            overrides: dict[str, object] = {
                "oauth_integration_id": oauth_integration.id
            }
            # Credentials already consumed into the OAuth client; don't persist them.
            if used_byo_credentials:
                overrides["custom_credentials"] = None
            create_params = params.model_copy(update=overrides)
            mcp_integration = await self.create_mcp_integration(params=create_params)
        oauth_connect = await self._start_custom_mcp_oauth_authorization(
            integration=oauth_integration,
            server_uri=mcp_integration.server_uri or params.server_uri,
            endpoints=endpoints,
            registration=registration,
        )
        return PlatformMCPCatalogConnectResult(
            mcp_integration=mcp_integration,
            oauth_connect=oauth_connect,
        )

    @classmethod
    def _mcp_oauth_client_registration_from_credentials(
        cls,
        *,
        params: MCPHttpIntegrationCreate,
        catalog_spec: MCPConnectionSpec | None,
    ) -> MCPOAuthRegistrationResult | None:
        if (
            catalog_spec is None
            or catalog_spec.server_type != "http"
            or catalog_spec.auth_type != MCPAuthType.OAUTH2
            or not params.custom_credentials
        ):
            return None
        if not any(
            field.target == "oauth_client"
            for field in [*catalog_spec.config_fields, *catalog_spec.credentials]
        ):
            return None

        raw_credentials = params.custom_credentials.get_secret_value().strip()
        if not raw_credentials:
            return None
        try:
            parsed = orjson.loads(raw_credentials)
        except orjson.JSONDecodeError as exc:
            raise ValueError("OAuth client credentials must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("OAuth client credentials must be a JSON object")

        entries = [
            (key, value.strip())
            for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, str) and value.strip()
        ]
        client_secret = next(
            (v for k, v in entries if cls._oauth_client_key_matches(k, "clientsecret")),
            None,
        )
        # Prefer an explicit client_id key; otherwise the first non-secret value.
        client_id = next(
            (v for k, v in entries if cls._oauth_client_key_matches(k, "clientid")),
            None,
        ) or next(
            (
                v
                for k, v in entries
                if not cls._oauth_client_key_matches(k, "clientsecret")
            ),
            None,
        )
        if not client_id:
            raise ValueError("OAuth client ID is required")
        return MCPOAuthRegistrationResult(
            client_id=client_id,
            client_secret=client_secret,
            auth_method=None,
        )

    @staticmethod
    def _oauth_client_key_matches(key: str, suffix: str) -> bool:
        """Match an OAuth client_id / client_secret key regardless of separators.

        ``suffix`` is the separator-free form, e.g. ``clientid`` or ``clientsecret``.
        """
        normalized = re.sub(r"[^a-z0-9]+", "", key.lower())
        return normalized in {suffix, f"oauth{suffix}"} or normalized.endswith(suffix)

    async def _mcp_integration_for_oauth_integration(
        self, *, oauth_integration_id: uuid.UUID
    ) -> MCPIntegration | None:
        result = await self.session.execute(
            select(MCPIntegration).where(
                MCPIntegration.workspace_id == self.workspace_id,
                MCPIntegration.oauth_integration_id == oauth_integration_id,
            )
        )
        return result.scalars().first()

    @require_scope("integration:create", "integration:update", require_all=False)
    async def complete_mcp_oauth_discovery_callback(
        self,
        *,
        provider_id: str,
        code: str,
        state: str,
        code_verifier: str | None,
    ) -> OAuthIntegration:
        provider_key = ProviderKey(
            id=provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        integration = await self.get_integration(provider_key=provider_key)
        if integration is None:
            raise ValueError("MCP OAuth integration not found")
        provider_config = self.get_provider_config(integration=integration)
        if provider_config is None or not provider_config.client_id:
            raise ValueError("MCP OAuth integration is missing client configuration")
        mcp_integration = await self._mcp_integration_for_oauth_integration(
            oauth_integration_id=integration.id
        )
        if mcp_integration is None or not mcp_integration.server_uri:
            raise ValueError("MCP OAuth integration is not linked to an MCP server")

        endpoints = await self._resolve_mcp_oauth_endpoints(
            server_uri=mcp_integration.server_uri,
            provider_config=provider_config,
        )
        client_secret = (
            provider_config.client_secret.get_secret_value()
            if provider_config.client_secret
            else None
        )
        callback_state = self._decode_mcp_oauth_callback_state(code_verifier)
        token_auth_method = self._mcp_token_auth_method(
            methods=endpoints.token_methods,
            client_secret=client_secret,
            registered_auth_method=callback_state.token_auth_method,
        )
        client = self._build_mcp_oauth_client(
            client_id=provider_config.client_id,
            client_secret=client_secret,
            token_auth_method=token_auth_method,
        )
        token_endpoint = provider_config.token_endpoint or endpoints.token_endpoint
        await validate_oauth_endpoint_resolves_public_async(token_endpoint)
        try:
            token = TokenResponse.from_oauth_response(
                await client.fetch_token(
                    token_endpoint,
                    code=code,
                    state=state,
                    code_verifier=callback_state.code_verifier,
                    resource=endpoints.resource,
                ),
                default_expires_in=None,
            )
        except ValueError as exc:
            raise ValueError(
                "MCP OAuth token response did not include access_token"
            ) from exc
        return await self.store_integration(
            provider_key=provider_key,
            user_id=self.role.user_id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
            scope=token.scope or None,
            authorization_endpoint=provider_config.authorization_endpoint
            or endpoints.authorization_endpoint,
            token_endpoint=token_endpoint,
            # Persist the method that just worked so refresh keeps using it
            # instead of re-deriving one the server may reject.
            token_endpoint_auth_method=token_auth_method,
        )

    async def _refresh_custom_mcp_integration(
        self,
        *,
        integration: OAuthIntegration,
        refresh_token: str,
    ) -> OAuthIntegration:
        provider_config = self.get_provider_config(integration=integration)
        if provider_config is None or not provider_config.client_id:
            return integration
        mcp_integration = await self._mcp_integration_for_oauth_integration(
            oauth_integration_id=integration.id
        )
        if mcp_integration is None or not mcp_integration.server_uri:
            return integration

        endpoints = await self._resolve_mcp_oauth_endpoints(
            server_uri=mcp_integration.server_uri,
            provider_config=provider_config,
        )
        client_secret = (
            provider_config.client_secret.get_secret_value()
            if provider_config.client_secret
            else None
        )
        token_auth_method = self._mcp_token_auth_method(
            methods=endpoints.token_methods,
            client_secret=client_secret,
            registered_auth_method=integration.token_endpoint_auth_method,
        )
        client = self._build_mcp_oauth_client(
            client_id=provider_config.client_id,
            client_secret=client_secret,
            token_auth_method=token_auth_method,
            with_redirect=False,
        )
        token_endpoint = provider_config.token_endpoint or endpoints.token_endpoint
        await validate_oauth_endpoint_resolves_public_async(token_endpoint)
        try:
            token = TokenResponse.from_oauth_response(
                await client.refresh_token(
                    token_endpoint,
                    refresh_token=refresh_token,
                    resource=endpoints.resource,
                ),
                default_refresh_token=refresh_token,
                default_expires_in=None,
                default_scope=integration.scope or "",
            )
        except ValueError:
            return integration
        integration.encrypted_access_token = self._encrypt_token(
            token.access_token.get_secret_value()
        )
        if token.refresh_token:
            integration.encrypted_refresh_token = self._encrypt_token(
                token.refresh_token.get_secret_value()
            )
        if token.expires_in is not None:
            integration.expires_at = datetime.now() + timedelta(
                seconds=token.expires_in
            )
        if token.scope:
            integration.scope = token.scope
        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    @staticmethod
    def _determine_endpoints(
        provider_impl: type[BaseOAuthProvider] | None,
        *,
        configured_authorization: str | None,
        configured_token: str | None,
    ) -> tuple[str | None, str | None]:
        """Determine effective OAuth endpoints from configured values or provider defaults."""

        default_auth = (
            getattr(provider_impl, "default_authorization_endpoint", None)
            if provider_impl
            else None
        )
        default_token = (
            getattr(provider_impl, "default_token_endpoint", None)
            if provider_impl
            else None
        )
        authorization_endpoint = IntegrationService._validate_https_endpoint(
            configured_authorization or default_auth,
            field_name="authorization_endpoint",
        )
        token_endpoint = IntegrationService._validate_https_endpoint(
            configured_token or default_token,
            field_name="token_endpoint",
        )
        return authorization_endpoint, token_endpoint

    @require_scope("integration:create", "integration:update", require_all=False)
    async def store_integration(
        self,
        *,
        provider_key: ProviderKey,
        user_id: UserID | None = None,
        access_token: SecretStr,
        refresh_token: SecretStr | None = None,
        expires_in: int | None = None,
        scope: str | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        token_endpoint_auth_method: str | None = None,
    ) -> OAuthIntegration:
        """Store or update a user's integration."""
        # Calculate expiration time if expires_in is provided
        expires_at = None
        if expires_in is not None:
            expires_at = datetime.now() + timedelta(seconds=expires_in)

        provider_impl = get_provider_class(provider_key)
        default_authorization = (
            getattr(provider_impl, "default_authorization_endpoint", None)
            if provider_impl
            else None
        )
        default_token = (
            getattr(provider_impl, "default_token_endpoint", None)
            if provider_impl
            else None
        )

        def resolve_endpoint(
            incoming: str | None, existing: str | None, default: str | None
        ) -> str | None:
            if incoming:
                return incoming
            if existing:
                return existing
            return default

        if integration := await self.get_integration(provider_key=provider_key):
            # Update existing integration
            integration.encrypted_access_token = self._encrypt_token(
                access_token.get_secret_value()
            )
            integration.encrypted_refresh_token = (
                self._encrypt_token(refresh_token.get_secret_value())
                if refresh_token
                else None
            )
            integration.expires_at = expires_at
            integration.scope = scope
            new_authorization_endpoint = resolve_endpoint(
                authorization_endpoint,
                integration.authorization_endpoint,
                default_authorization,
            )
            integration.authorization_endpoint = self._validate_https_endpoint(
                new_authorization_endpoint,
                field_name="authorization_endpoint",
            )
            new_token_endpoint = resolve_endpoint(
                token_endpoint,
                integration.token_endpoint,
                default_token,
            )
            integration.token_endpoint = self._validate_https_endpoint(
                new_token_endpoint,
                field_name="token_endpoint",
            )
            # Always sync to the method used by the exchange that just
            # succeeded; a retained stale method (e.g. after the client lost
            # its secret) would make refresh send client auth the server
            # rejects.
            integration.token_endpoint_auth_method = token_endpoint_auth_method

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Updated user integration",
                user_id=user_id,
                provider=provider_key,
            )
        else:
            # Create new integration
            integration = OAuthIntegration(
                workspace_id=self.workspace_id,
                user_id=user_id,
                provider_id=provider_key.id,
                grant_type=provider_key.grant_type,
                encrypted_access_token=self._encrypt_token(
                    access_token.get_secret_value()
                ),
                encrypted_refresh_token=self._encrypt_token(
                    refresh_token.get_secret_value()
                )
                if refresh_token
                else None,
                expires_at=expires_at,
                scope=scope,
                authorization_endpoint=self._validate_https_endpoint(
                    resolve_endpoint(
                        authorization_endpoint,
                        None,
                        default_authorization,
                    ),
                    field_name="authorization_endpoint",
                ),
                token_endpoint=self._validate_https_endpoint(
                    resolve_endpoint(token_endpoint, None, default_token),
                    field_name="token_endpoint",
                ),
                token_endpoint_auth_method=token_endpoint_auth_method,
            )

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Created user integration",
                user_id=user_id,
                provider=provider_key,
            )
        # Auto-create MCP integration for MCP providers when properly connected
        await self._auto_create_mcp_integration_if_needed(
            integration=integration, provider_key=provider_key
        )
        return integration

    @require_scope("integration:update")
    async def disconnect_integration(self, *, integration: OAuthIntegration) -> None:
        """Disconnect a user's integration for a specific provider."""
        try:
            if await self._is_mcp_lifecycle_owned_oauth_integration(
                integration=integration
            ):
                await self._delete_mcp_integrations_for_oauth_integration(
                    integration=integration
                )
            self._disconnect_integration_state(integration=integration)
            self.session.add(integration)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    def _disconnect_integration_state(self, *, integration: OAuthIntegration) -> None:
        """Apply disconnected token state to an integration without committing."""
        integration.encrypted_access_token = b""
        integration.encrypted_refresh_token = None
        integration.expires_at = None
        integration.scope = None  # Granted scopes
        integration.requested_scopes = None

    @require_scope("integration:delete")
    async def remove_integration(self, *, integration: OAuthIntegration) -> None:
        """Remove a user's integration for a specific provider."""
        # Capture provider info before deleting
        provider_key = ProviderKey(
            id=integration.provider_id, grant_type=integration.grant_type
        )
        is_custom_provider = integration.provider_id.startswith("custom_")

        # Delete the integration record
        await self.session.delete(integration)
        await self.session.commit()

        # If this is a custom provider, also delete the custom provider definition
        if is_custom_provider:
            await self.delete_custom_provider(provider_key=provider_key)

    async def refresh_token_if_needed(
        self, integration: OAuthIntegration
    ) -> OAuthIntegration:
        """Refresh the access token if it's expired or about to expire."""
        if not integration.needs_refresh:
            return integration

        try:
            if integration.grant_type == OAuthGrantType.AUTHORIZATION_CODE:
                integration = await self._refresh_ac_integration(integration)
            elif integration.grant_type == OAuthGrantType.CLIENT_CREDENTIALS:
                integration = await self._refresh_cc_integration(integration)
            else:
                self.logger.warning(
                    "Unsupported grant type for refresh",
                    grant_type=integration.grant_type,
                    provider=integration.provider_id,
                )
                return integration
        except Exception as e:
            self.logger.error(
                "Failed to refresh token, continuing with current token",
                error=str(e),
                provider=integration.provider_id,
                expires_at=integration.expires_at,
            )
            # Return unchanged - let it fail naturally when token expires
            return integration

        await self.session.refresh(integration)
        return integration

    async def _provider_from_integration(
        self, integration: OAuthIntegration
    ) -> BaseOAuthProvider | None:
        # Get provider class from registry
        key = ProviderKey(id=integration.provider_id, grant_type=integration.grant_type)
        provider_impl = await self.resolve_provider_impl(provider_key=key)
        if not provider_impl:
            self.logger.error(
                "Provider not found",
                provider=integration.provider_id,
            )
            return None

        # Create provider instance from integration config
        try:
            # Decrypt client credentials if using workspace credentials
            client_id = (
                self._decrypt_token(integration.encrypted_client_id)
                if integration.encrypted_client_id
                else None
            )
            client_secret = (
                self._decrypt_token(integration.encrypted_client_secret)
                if integration.encrypted_client_secret
                else None
            )

            if not client_id:
                self.logger.warning(
                    "No client ID found",
                    user_id=integration.user_id,
                    provider=integration.provider_id,
                )
                if not issubclass(provider_impl, MCPAuthProvider):
                    return None

            authorization_endpoint, token_endpoint = self._determine_endpoints(
                provider_impl,
                configured_authorization=integration.authorization_endpoint,
                configured_token=integration.token_endpoint,
            )
            # Create provider config
            provider_config = ProviderConfig(
                client_id=client_id,
                client_secret=SecretStr(client_secret)
                if client_secret is not None
                else None,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                scopes=self.parse_scopes(integration.requested_scopes),
            )
            return await provider_impl.instantiate(config=provider_config)
        except Exception as e:
            self.logger.error(
                "Failed to create provider for token refresh",
                user_id=integration.user_id,
                provider=integration.provider_id,
                error=str(e),
            )
            return None

    async def _refresh_cc_integration(
        self, integration: OAuthIntegration
    ) -> OAuthIntegration:
        """Refresh an integration using the client credentials for client credentials grant type."""
        provider = await self._provider_from_integration(integration)
        if not provider:
            self.logger.warning("Provider not found", provider=integration.provider_id)
            return integration
        if not isinstance(provider, ClientCredentialsOAuthProvider):
            self.logger.warning(
                "Provider does not support client credentials",
                provider=integration.provider_id,
            )
            return integration
        token_response = await provider.get_client_credentials_token()
        # Update integration with new tokens
        integration.encrypted_access_token = self._encrypt_token(
            token_response.access_token.get_secret_value()
        )

        # Update refresh token if provider rotated it
        if token_response.refresh_token:
            integration.encrypted_refresh_token = self._encrypt_token(
                token_response.refresh_token.get_secret_value()
            )

        # Update expiry time
        integration.expires_at = (
            datetime.now() + timedelta(seconds=token_response.expires_in)
            if token_response.expires_in is not None
            else None
        )

        # Update scope if changed
        integration.scope = token_response.scope

        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    async def _refresh_ac_integration(
        self, integration: OAuthIntegration
    ) -> OAuthIntegration:
        """Refresh an integration using the refresh token for authorization code grant type."""
        # Check if refresh token exists by attempting to decrypt
        try:
            refresh_token = (
                self._decrypt_token(integration.encrypted_refresh_token)
                if integration.encrypted_refresh_token
                else None
            )
        except Exception as e:
            self.logger.error(
                "Failed to decrypt refresh token",
                user_id=integration.user_id,
                provider=integration.provider_id,
                error=str(e),
            )
            return integration

        if not refresh_token:
            self.logger.warning(
                "Integration needs refresh but no refresh token available",
                user_id=integration.user_id,
                provider=integration.provider_id,
            )
            return integration

        if self._is_custom_mcp_oauth_provider(integration.provider_id):
            try:
                return await self._refresh_custom_mcp_integration(
                    integration=integration,
                    refresh_token=refresh_token,
                )
            except Exception as e:
                self.logger.error(
                    "Failed to refresh generic MCP OAuth token",
                    user_id=integration.user_id,
                    provider=integration.provider_id,
                    error=str(e),
                )
                return integration

        provider = await self._provider_from_integration(integration)
        if not provider:
            self.logger.warning(
                "Provider not found in registry, cannot refresh",
                user_id=integration.user_id,
                provider=integration.provider_id,
            )
            return integration

        if not isinstance(provider, AuthorizationCodeOAuthProvider):
            self.logger.warning(
                "Provider does not support token refresh",
                user_id=integration.user_id,
                provider=integration.provider_id,
            )
            return integration

        # Refresh the access token
        try:
            token_response = await provider.refresh_access_token(refresh_token)

            # Update integration with new tokens
            integration.encrypted_access_token = self._encrypt_token(
                token_response.access_token.get_secret_value()
            )

            # Update refresh token if provider rotated it
            if token_response.refresh_token:
                integration.encrypted_refresh_token = self._encrypt_token(
                    token_response.refresh_token.get_secret_value()
                )

            # Update expiry time
            if token_response.expires_in is not None:
                integration.expires_at = datetime.now() + timedelta(
                    seconds=token_response.expires_in
                )

            # Update scope if changed
            integration.scope = token_response.scope

            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Successfully updated integration with refreshed tokens",
                user_id=integration.user_id,
                provider=integration.provider_id,
            )

        except Exception as e:
            self.logger.error(
                "Failed to refresh access token",
                user_id=integration.user_id,
                provider=integration.provider_id,
                error=str(e),
            )
            # Return unchanged integration instead of raising
            return integration

        return integration

    @require_scope("integration:read")
    async def get_access_token(self, integration: OAuthIntegration) -> SecretStr | None:
        """Get the decrypted access token for an integration."""
        if access_token := self._decrypt_token(integration.encrypted_access_token):
            return SecretStr(access_token)
        return None

    @require_scope("integration:read")
    def get_decrypted_tokens(
        self, integration: OAuthIntegration
    ) -> tuple[str | None, str | None]:
        """Get decrypted access and refresh tokens for an integration."""
        access_token = self._decrypt_token(integration.encrypted_access_token)
        refresh_token = (
            self._decrypt_token(integration.encrypted_refresh_token)
            if integration.encrypted_refresh_token
            else None
        )
        return access_token, refresh_token

    def _encrypt_token(self, token: str) -> bytes:
        """Encrypt a token using the service's encryption key."""
        return encrypt_value(token.encode("utf-8"), key=self._encryption_key)

    def _decrypt_token(self, encrypted_token: bytes) -> str | None:
        """Decrypt a token using the service's encryption key."""
        if not is_set(encrypted_token):
            return None
        return decrypt_value(encrypted_token, key=self._encryption_key).decode("utf-8")

    def encrypt_client_credential(self, credential: str) -> bytes:
        """Encrypt a client credential using the service's encryption key."""
        return encrypt_value(credential.encode("utf-8"), key=self._encryption_key)

    def decrypt_client_credential(self, encrypted_credential: bytes) -> str:
        """Decrypt a client credential using the service's encryption key."""
        return decrypt_value(encrypted_credential, key=self._encryption_key).decode(
            "utf-8"
        )

    @require_scope("integration:read")
    def decrypt_stdio_env(
        self, mcp_integration: MCPIntegration
    ) -> dict[str, str] | None:
        """Decrypt and return stdio_env for an MCP integration."""
        if not mcp_integration.encrypted_stdio_env:
            return None
        if not is_set(mcp_integration.encrypted_stdio_env):
            return None
        decrypted = self._decrypt_token(mcp_integration.encrypted_stdio_env)
        if not decrypted:
            return None
        loaded = orjson.loads(decrypted)
        if not isinstance(loaded, dict):
            return None

        env: dict[str, str] = {}
        for key, value in loaded.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return None
            env[key] = value
        return env

    @require_scope("integration:create", "integration:update", require_all=False)
    async def store_provider_config(
        self,
        *,
        provider_key: ProviderKey,
        client_id: str | None = None,
        client_secret: SecretStr | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        requested_scopes: list[str] | None = None,
    ) -> OAuthIntegration:
        """Store or update provider configuration (client credentials) for a workspace."""
        # Check if integration configuration already exists for this provider

        provider_impl = await self.resolve_provider_impl(provider_key=provider_key)
        normalized_scopes = self._normalize_scopes(requested_scopes)
        resolved_authorization, resolved_token = self._determine_endpoints(
            provider_impl,
            configured_authorization=authorization_endpoint,
            configured_token=token_endpoint,
        )

        if integration := await self.get_integration(provider_key=provider_key):
            # Update existing integration with client credentials (patch operation)
            if (
                client_id is None
                and client_secret is None
                and authorization_endpoint is None
                and token_endpoint is None
                and requested_scopes is None
            ):
                return integration

            if client_id is not None:
                integration.encrypted_client_id = self.encrypt_client_credential(
                    client_id
                )

            if client_secret is not None:
                integration.encrypted_client_secret = self.encrypt_client_credential(
                    client_secret.get_secret_value()
                )

            integration.authorization_endpoint = self._validate_https_endpoint(
                authorization_endpoint
                or integration.authorization_endpoint
                or resolved_authorization,
                field_name="authorization_endpoint",
            )
            integration.token_endpoint = self._validate_https_endpoint(
                token_endpoint or integration.token_endpoint or resolved_token,
                field_name="token_endpoint",
            )

            if requested_scopes is not None:
                integration.requested_scopes = (
                    " ".join(normalized_scopes) if normalized_scopes else ""
                )

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Updated provider configuration",
                provider=provider_key,
                workspace_id=self.workspace_id,
            )

            return integration
        else:
            # Create new integration record with just client credentials
            # Access tokens will be added later during OAuth flow
            integration = OAuthIntegration(
                workspace_id=self.workspace_id,
                provider_id=provider_key.id,
                grant_type=provider_key.grant_type,
                encrypted_client_id=self.encrypt_client_credential(client_id)
                if client_id
                else None,
                encrypted_client_secret=self.encrypt_client_credential(
                    client_secret.get_secret_value()
                )
                if client_secret
                else None,
                use_workspace_credentials=True,
                # These will be populated during OAuth flow
                encrypted_access_token=b"",  # Placeholder, will be updated
                authorization_endpoint=self._validate_https_endpoint(
                    resolved_authorization,
                    field_name="authorization_endpoint",
                ),
                token_endpoint=self._validate_https_endpoint(
                    resolved_token,
                    field_name="token_endpoint",
                ),
                requested_scopes=(
                    " ".join(normalized_scopes)
                    if requested_scopes is not None
                    else None
                )
                if normalized_scopes
                else ("" if requested_scopes is not None else None),
            )

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Created provider configuration",
                provider=provider_key,
                workspace_id=self.workspace_id,
            )
            return integration

    def get_provider_config(
        self,
        *,
        integration: OAuthIntegration,
        provider_impl: type[BaseOAuthProvider] | None = None,
        default_scopes: list[str] | None = None,
    ) -> ProviderConfig | None:
        """Get decrypted client credentials for a provider."""

        if not integration or not integration.use_workspace_credentials:
            return None

        if not integration.encrypted_client_id:
            return None

        try:
            client_id = self.decrypt_client_credential(integration.encrypted_client_id)
            client_secret = (
                self.decrypt_client_credential(integration.encrypted_client_secret)
                if integration.encrypted_client_secret
                else None
            )
            authorization_endpoint, token_endpoint = self._determine_endpoints(
                provider_impl,
                configured_authorization=integration.authorization_endpoint,
                configured_token=integration.token_endpoint,
            )
            return ProviderConfig(
                client_id=client_id,
                client_secret=SecretStr(client_secret)
                if client_secret is not None
                else None,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                scopes=self.parse_scopes(integration.requested_scopes)
                or default_scopes,
            )
        except InsecureOAuthEndpointError as e:
            self.logger.error(
                "Rejected insecure OAuth endpoint",
                provider=integration.provider_id,
                workspace_id=self.workspace_id,
                error=str(e),
            )
            return None
        except Exception as e:
            self.logger.error(
                "Failed to decrypt client credentials",
                provider=integration.provider_id,
                workspace_id=self.workspace_id,
                error=str(e),
            )
            return None

    @require_scope("integration:delete")
    async def remove_provider_config(self, *, provider_key: ProviderKey) -> bool:
        """Remove provider configuration (client credentials) for a workspace."""
        integration = await self.get_integration(provider_key=provider_key)

        if not integration:
            return False

        # If integration has tokens, just clear client credentials
        if is_set(integration.encrypted_access_token):
            integration.encrypted_client_id = None
            integration.encrypted_client_secret = None
            integration.use_workspace_credentials = False

            self.session.add(integration)
            await self.session.commit()

            self.logger.info(
                "Removed provider configuration, kept tokens",
                provider=provider_key,
                workspace_id=self.workspace_id,
            )
        else:
            # No tokens, remove entire integration record
            await self.session.delete(integration)
            await self.session.commit()

            self.logger.info(
                "Removed provider configuration completely",
                provider=provider_key,
                workspace_id=self.workspace_id,
            )

        return True

    def parse_scopes(self, scopes: str | None) -> list[str] | None:
        """Parse a space-separated string of scopes into a list of scopes."""
        return scopes.split(" ") if scopes else None

    async def _auto_create_mcp_integration_if_needed(
        self,
        *,
        integration: OAuthIntegration,
        provider_key: ProviderKey,
    ) -> None:
        """Auto-create MCP integration for MCP OAuth providers.

        When an OAuth integration is created/updated for an MCP provider,
        automatically create or update the corresponding MCPIntegration record.
        Only creates MCP integration if the OAuth integration is properly connected
        (has access tokens).
        """
        # Check if integration is properly connected (has access tokens)
        if not is_set(integration.encrypted_access_token):
            return

        # Check if provider is an MCP provider
        provider_impl = await self.resolve_provider_impl(provider_key=provider_key)
        if provider_impl is None:
            return

        is_mcp_provider = issubclass(provider_impl, MCPAuthProvider)
        if not is_mcp_provider:
            return
        mcp_provider_impl = cast(type[MCPAuthProvider], provider_impl)

        catalog_entry = get_platform_mcp_catalog_entry_by_provider_id(
            mcp_provider_impl.id,
            include_private=True,
        )
        mcp_integration: MCPIntegration | None = None
        if catalog_entry is not None:
            existing_catalog_mcp = await self.session.execute(
                select(MCPIntegration).where(
                    MCPIntegration.workspace_id == self.workspace_id,
                    MCPIntegration.catalog_slug == catalog_entry.slug,
                )
            )
            mcp_integration = existing_catalog_mcp.scalars().first()

        # Check if MCP integration already exists for this OAuth integration.
        # Legacy provider-generated rows predate catalog_slug, so they are
        # picked up here and stamped only after matching provider shape below.
        if mcp_integration is None:
            existing_mcp = await self.session.execute(
                select(MCPIntegration).where(
                    MCPIntegration.oauth_integration_id == integration.id,
                    MCPIntegration.workspace_id == self.workspace_id,
                )
            )
            mcp_integration = existing_mcp.scalars().first()

        if mcp_integration is None:
            if not await self.has_entitlement(Entitlement.AGENT_ADDONS):
                self.logger.info(
                    "Skipped MCP provider auto-create due to missing entitlement",
                    provider=provider_key.id,
                    workspace_id=self.workspace_id,
                )
                return

            # Create new MCP integration
            metadata = mcp_provider_impl.metadata

            # Use provider ID as slug to preserve underscores for icon mapping
            slug = mcp_provider_impl.id
            if await self._mcp_integration_slug_taken(slug):
                slug = await self._generate_mcp_integration_slug(
                    name=metadata.name,
                    requested_slug=mcp_provider_impl.id,
                    requested_slug_separator="_",
                )

            mcp_integration = MCPIntegration(
                workspace_id=self.workspace_id,
                # Keep provider metadata display names as-is (including "MCP" suffixes).
                name=metadata.name,
                description=metadata.description,
                slug=slug,
                catalog_slug=catalog_entry.slug if catalog_entry else None,
                server_uri=mcp_provider_impl.mcp_server_uri,
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
            )
            self.session.add(mcp_integration)
            await self.session.commit()
            await self.session.refresh(mcp_integration)

            self.logger.info(
                "Auto-created MCP integration for MCP provider",
                mcp_integration_id=mcp_integration.id,
                provider=provider_key.id,
                oauth_integration_id=integration.id,
            )
        else:
            updated = False
            # Update existing MCP integration to ensure it references the OAuth integration
            if mcp_integration.oauth_integration_id != integration.id:
                mcp_integration.oauth_integration_id = integration.id
                updated = True
            if (
                catalog_entry is not None
                and mcp_integration.catalog_slug is None
                and self._mcp_integration_uses_provider_server(
                    mcp_integration,
                    mcp_provider_impl,
                )
                and self._mcp_integration_has_provider_slug(
                    mcp_integration,
                    mcp_provider_impl,
                )
            ):
                mcp_integration.catalog_slug = catalog_entry.slug
                updated = True
            if updated:
                self.session.add(mcp_integration)
                await self.session.commit()

                self.logger.info(
                    "Updated MCP integration OAuth reference",
                    mcp_integration_id=mcp_integration.id,
                    oauth_integration_id=integration.id,
                )

    async def _generate_mcp_integration_slug(
        self,
        *,
        name: str,
        requested_slug: str | None = None,
        requested_slug_separator: str = "-",
    ) -> str:
        """Generate a unique slug for an MCP integration."""
        if requested_slug:
            slug = (
                slugify(requested_slug, separator=requested_slug_separator)
                or uuid4().hex[:8]
            )
        else:
            slug = slugify(name, separator="-") or uuid4().hex[:8]
            catalog_slugs = {
                entry.slug
                for entry in get_platform_mcp_catalog_entries(include_private=True)
            }
            if slug in catalog_slugs:
                slug = f"{slug}-custom"

        # Truncate to max length, leaving room for suffix if needed
        max_base_length = MAX_SERVER_NAME_LENGTH - 4  # Reserve space for "-999"
        if len(slug) > max_base_length:
            slug = slug[:max_base_length].rstrip("-")

        candidate = slug
        suffix = 1
        while await self._mcp_integration_slug_taken(candidate):
            candidate = f"{slug}-{suffix}"
            suffix += 1
        return candidate

    async def _is_mcp_lifecycle_owned_oauth_integration(
        self, *, integration: OAuthIntegration
    ) -> bool:
        """Return whether OAuth integration is owned by MCP provider lifecycle."""
        if self._is_custom_mcp_oauth_provider(integration.provider_id):
            return True
        provider_impl = await self.resolve_provider_impl(
            provider_key=ProviderKey(
                id=integration.provider_id,
                grant_type=integration.grant_type,
            )
        )
        return bool(provider_impl and issubclass(provider_impl, MCPAuthProvider))

    async def _delete_mcp_integrations_for_oauth_integration(
        self, *, integration: OAuthIntegration
    ) -> int:
        """Delete MCP rows backed by a lifecycle-owned OAuth integration."""
        filters = [
            MCPIntegration.workspace_id == self.workspace_id,
            MCPIntegration.oauth_integration_id == integration.id,
            MCPIntegration.server_type == "http",
            MCPIntegration.auth_type == MCPAuthType.OAUTH2,
        ]
        # Custom MCP providers own every row linked to them; provider-backed rows
        # must additionally match the provider's server URI and generated slug.
        if not self._is_custom_mcp_oauth_provider(integration.provider_id):
            provider_impl = await self.resolve_provider_impl(
                provider_key=ProviderKey(
                    id=integration.provider_id,
                    grant_type=integration.grant_type,
                )
            )
            if provider_impl is None or not issubclass(provider_impl, MCPAuthProvider):
                return 0
            provider_slug = provider_impl.id
            escaped_provider_slug = self._escape_like_pattern(provider_slug)
            filters += [
                MCPIntegration.server_uri == provider_impl.mcp_server_uri,
                or_(
                    MCPIntegration.slug == provider_slug,
                    and_(
                        MCPIntegration.slug.like(
                            f"{escaped_provider_slug}-%", escape="\\"
                        ),
                        sa.func.substring(
                            MCPIntegration.slug, len(provider_slug) + 2
                        ).op("~")(r"^\d+$"),
                    ),
                ),
            ]

        candidate_mcp_integrations = (
            select(
                MCPIntegration.id.label("id"),
                sa.cast(MCPIntegration.id, sa.String).label("id_str"),
            )
            .where(*filters)
            .cte("candidate_mcp_integrations")
        )

        candidate_ids = select(
            sa.func.coalesce(
                sa.func.array_agg(candidate_mcp_integrations.c.id_str),
                sa.cast(sa.literal([]), sa.ARRAY(sa.String())),
            )
        ).scalar_subquery()
        candidate_exists = select(candidate_mcp_integrations.c.id).exists()

        pruned_preset_ids = (
            await self.session.scalars(
                update(AgentPreset)
                .where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.mcp_integrations.isnot(None),
                    candidate_exists,
                    AgentPreset.mcp_integrations.op("?|")(candidate_ids),
                )
                .values(
                    mcp_integrations=AgentPreset.mcp_integrations.op("-")(candidate_ids)
                )
                .returning(AgentPreset.id)
                .execution_options(synchronize_session="fetch")
            )
        ).all()
        await self._version_pruned_agent_presets(preset_ids=pruned_preset_ids)

        await self.session.execute(
            update(AgentSession)
            .where(
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.mcp_integrations.isnot(None),
                candidate_exists,
                AgentSession.mcp_integrations.op("?|")(candidate_ids),
            )
            .values(
                mcp_integrations=AgentSession.mcp_integrations.op("-")(candidate_ids)
            )
            .execution_options(synchronize_session="fetch")
        )

        deleted = await self.session.scalars(
            sa.delete(MCPIntegration)
            .where(MCPIntegration.id.in_(select(candidate_mcp_integrations.c.id)))
            .returning(MCPIntegration.id)
            .execution_options(synchronize_session="fetch")
        )
        return len(deleted.all())

    async def _version_pruned_agent_presets(
        self, *, preset_ids: Sequence[uuid.UUID]
    ) -> None:
        """Create current versions for presets whose MCP refs were pruned."""
        if not preset_ids:
            return

        from tracecat.agent.preset.service import AgentPresetService

        preset_service = AgentPresetService(self.session, role=self.role)
        presets = await self.session.scalars(
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id.in_(preset_ids),
            )
            .order_by(AgentPreset.id)
            .with_for_update()
        )

        for preset in presets:
            version = await preset_service._create_version_from_preset(
                preset,
                preset_locked=True,
            )
            preset.current_version_id = version.id
            self.session.add(preset)
        await self.session.flush()

    async def _mcp_integration_slug_taken(self, slug: str) -> bool:
        """Check if an MCP integration slug is already taken."""
        statement = select(MCPIntegration).where(
            MCPIntegration.workspace_id == self.workspace_id,
            MCPIntegration.slug == slug,
        )
        result = await self.session.execute(statement)
        return result.scalars().first() is not None

    async def _resolve_create_platform_mcp_catalog(
        self, *, params: MCPIntegrationCreate
    ) -> PlatformMCPCatalogEntry | None:
        """Resolve the catalog row for catalog-backed create payloads."""
        if params.catalog_slug is None:
            return None

        catalog_entry = get_platform_mcp_catalog_entry_by_slug(
            params.catalog_slug,
            include_private=True,
        )
        if catalog_entry is None:
            raise ValueError("Platform MCP catalog row not found")
        if catalog_entry.status != "available":
            raise ValueError(f"{catalog_entry.name} is not available to connect")
        if not self._params_match_catalog_connection_specs(
            params=params, catalog_entry=catalog_entry
        ):
            raise ValueError(
                f"Requested server and auth configuration does not match any "
                f"connection option for {catalog_entry.name}"
            )

        await self.require_entitlement(Entitlement.AGENT_ADDONS)
        return catalog_entry

    @staticmethod
    def _params_match_catalog_connection_specs(
        *,
        params: MCPIntegrationCreate,
        catalog_entry: PlatformMCPCatalogEntry,
    ) -> bool:
        """Whether create params match one of a catalog row's connect recipes.

        Guards against binding an arbitrary payload to a platform catalog row
        (e.g. an auth-less row spoofing an OAuth-only connector as connected).
        HTTP params must match a spec's server and auth type; stdio create
        params carry no auth type (credentials ride in ``stdio_env``), so any
        stdio spec the row offers is accepted.
        """
        specs: list[MCPConnectionSpec] = []
        if catalog_entry.connection_spec is not None:
            specs.append(catalog_entry.connection_spec)
        specs.extend(
            option.connection_spec for option in catalog_entry.connection_options or []
        )
        for spec in specs:
            if spec.server_type != params.server_type:
                continue
            if params.server_type == "stdio" or spec.auth_type == params.auth_type:
                return True
        return False

    @require_scope("integration:create")
    async def create_mcp_integration(
        self, *, params: MCPIntegrationCreate
    ) -> MCPIntegration:
        """Create a new MCP integration."""
        catalog_row = await self._resolve_create_platform_mcp_catalog(params=params)
        slug = await self._generate_mcp_integration_slug(
            name=params.name,
            requested_slug=catalog_row.slug if catalog_row else None,
        )

        # Normalize server-type specific fields using discriminator narrowing.
        server_uri: str | None = None
        auth_type = MCPAuthType.NONE
        oauth_integration_id: uuid.UUID | None = None
        encrypted_custom_credentials: bytes | None = None
        stdio_command: str | None = None
        stdio_args: list[str] | None = None
        encrypted_stdio_env: bytes | None = None

        if params.server_type == "http":
            # Validate OAuth integration if auth_type is oauth2
            if params.auth_type == MCPAuthType.OAUTH2:
                if not params.oauth_integration_id and params.catalog_slug is None:
                    raise ValueError(
                        "oauth_integration_id is required for OAuth 2.0 authentication"
                    )
                if params.oauth_integration_id:
                    oauth_integration = await self.session.get(
                        OAuthIntegration, params.oauth_integration_id
                    )
                    if (
                        not oauth_integration
                        or oauth_integration.workspace_id != self.workspace_id
                    ):
                        raise ValueError(
                            "OAuth integration not found or does not belong to workspace"
                        )

            server_uri = params.server_uri.strip()
            auth_type = params.auth_type
            oauth_integration_id = params.oauth_integration_id
            if (
                params.auth_type in {MCPAuthType.CUSTOM, MCPAuthType.OAUTH2}
                and params.custom_credentials
            ):
                custom_credentials = params.custom_credentials.get_secret_value()
                if custom_credentials:
                    encrypted_custom_credentials = self._encrypt_token(
                        custom_credentials
                    )
        else:
            self._validate_stdio_server_config(
                command=params.stdio_command,
                args=params.stdio_args,
                env=params.stdio_env,
            )
            stdio_command = params.stdio_command
            stdio_args = params.stdio_args
            if params.stdio_env:
                encrypted_stdio_env = self._encrypt_token(
                    orjson.dumps(params.stdio_env).decode()
                )

        mcp_integration = MCPIntegration(
            workspace_id=self.workspace_id,
            name=params.name.strip(),
            description=params.description.strip() if params.description else None,
            slug=slug,
            catalog_slug=catalog_row.slug if catalog_row else None,
            server_uri=server_uri,
            auth_type=auth_type,
            oauth_integration_id=oauth_integration_id,
            encrypted_headers=encrypted_custom_credentials,  # Reuse field for custom credentials
            server_type=params.server_type,
            stdio_command=stdio_command,
            stdio_args=stdio_args,
            encrypted_stdio_env=encrypted_stdio_env,
            timeout=params.timeout,
        )

        self.session.add(mcp_integration)
        await self.session.commit()
        await self.session.refresh(mcp_integration)

        self.logger.info(
            "Created MCP integration",
            mcp_integration_id=mcp_integration.id,
            name=params.name,
            auth_type=auth_type,
            server_type=params.server_type,
        )

        return mcp_integration

    async def _mcp_oauth_integration_is_connected(
        self, *, mcp_integration: MCPIntegration
    ) -> bool:
        if mcp_integration.oauth_integration_id is None:
            return False
        oauth_integration = await self.session.get(
            OAuthIntegration, mcp_integration.oauth_integration_id
        )
        return bool(
            oauth_integration
            and oauth_integration.workspace_id == self.workspace_id
            and is_set(oauth_integration.encrypted_access_token)
        )

    async def _start_existing_custom_mcp_oauth(
        self, *, mcp_integration: MCPIntegration
    ) -> PlatformMCPCatalogConnectResult | None:
        if (
            mcp_integration.oauth_integration_id is None
            or not mcp_integration.server_uri
        ):
            return None
        oauth_integration = await self.session.get(
            OAuthIntegration, mcp_integration.oauth_integration_id
        )
        if (
            oauth_integration is None
            or oauth_integration.workspace_id != self.workspace_id
            or not self._is_custom_mcp_oauth_provider(oauth_integration.provider_id)
        ):
            return None
        provider_config = self.get_provider_config(integration=oauth_integration)
        if provider_config is None or not provider_config.client_id:
            return None

        endpoints = await self._resolve_mcp_oauth_endpoints(
            server_uri=mcp_integration.server_uri,
            provider_config=provider_config,
        )
        client_secret = (
            provider_config.client_secret.get_secret_value()
            if provider_config.client_secret
            else None
        )
        oauth_connect = await self._start_custom_mcp_oauth_authorization(
            integration=oauth_integration,
            server_uri=mcp_integration.server_uri,
            endpoints=endpoints,
            registration=MCPOAuthRegistrationResult(
                client_id=provider_config.client_id,
                client_secret=client_secret,
                auth_method=None,
            ),
        )
        return PlatformMCPCatalogConnectResult(
            mcp_integration=mcp_integration,
            oauth_connect=oauth_connect,
        )

    @require_scope("integration:create")
    async def connect_platform_mcp_catalog(
        self, *, catalog_slug: str
    ) -> PlatformMCPCatalogConnectResult:
        """Create or return the workspace MCP row for a catalog entry.

        Runtime catalog recipes are the primary path. Provider-backed OAuth is
        retained as an exception/legacy fallback for rows without a generic
        connection spec.
        """
        catalog = get_platform_mcp_catalog_entry_by_slug(
            catalog_slug, include_private=True
        )
        if catalog is None:
            raise ValueError("Platform MCP catalog row not found")
        if catalog.status != "available":
            raise ValueError(f"{catalog.name} is not available to connect")

        existing = await self._get_mcp_integration_by_catalog(catalog)
        if existing is not None:
            if existing.auth_type == MCPAuthType.OAUTH2:
                if await self._mcp_oauth_integration_is_connected(
                    mcp_integration=existing
                ):
                    return PlatformMCPCatalogConnectResult(mcp_integration=existing)
                # Re-establishing auth on an existing (e.g. migrated) catalog row
                # is a reconnect, gated the same as a fresh catalog connect.
                # Unentitled workspaces keep connected rows and may disconnect,
                # but must reconnect as a custom MCP server.
                await self.require_entitlement(Entitlement.AGENT_ADDONS)
                if custom_connect := await self._start_existing_custom_mcp_oauth(
                    mcp_integration=existing
                ):
                    return custom_connect
                spec = self._catalog_connection_spec(catalog)
                if spec and spec.server_type == "http" and existing.server_uri:
                    return await self.connect_mcp_oauth_discovery(
                        params=MCPHttpIntegrationCreate(
                            name=existing.name,
                            description=existing.description,
                            timeout=existing.timeout or 30,
                            catalog_slug=catalog.slug,
                            server_type="http",
                            server_uri=existing.server_uri,
                            auth_type=MCPAuthType.OAUTH2,
                        ),
                        catalog_spec=spec,
                        existing_mcp_integration=existing,
                    )
                if provider_connect := await self._start_catalog_provider_oauth(
                    catalog=catalog,
                    existing_mcp_integration=existing,
                ):
                    return provider_connect
            return PlatformMCPCatalogConnectResult(mcp_integration=existing)

        await self.require_entitlement(Entitlement.AGENT_ADDONS)

        spec = self._catalog_connection_spec(catalog)
        if spec and spec.server_type == "http" and spec.auth_type == MCPAuthType.OAUTH2:
            if self._catalog_requires_user_config(spec):
                raise ValueError(
                    f"{catalog.name} requires configuration before connect"
                )
            return await self.connect_mcp_oauth_discovery(
                params=MCPHttpIntegrationCreate(
                    name=catalog.name,
                    description=catalog.description,
                    timeout=30,
                    catalog_slug=catalog.slug,
                    server_type="http",
                    server_uri=spec.server_uri,
                    auth_type=MCPAuthType.OAUTH2,
                ),
                catalog_spec=spec,
            )

        if spec is not None:
            params = self._catalog_connect_create_params(catalog=catalog, spec=spec)
            return PlatformMCPCatalogConnectResult(
                mcp_integration=await self.create_mcp_integration(params=params)
            )

        if provider_connect := await self._start_catalog_provider_oauth(
            catalog=catalog
        ):
            return provider_connect

        raise ValueError(f"{catalog.name} is not connectable yet")

    async def _start_catalog_provider_oauth(
        self,
        *,
        catalog: PlatformMCPCatalogEntry,
        existing_mcp_integration: MCPIntegration | None = None,
    ) -> PlatformMCPCatalogConnectResult | None:
        provider_id = catalog.provider_id
        if not provider_id:
            return None
        provider_key = ProviderKey(
            id=provider_id,
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        provider_impl = await self.resolve_provider_impl(provider_key=provider_key)
        if provider_impl is None:
            raise ValueError(f"Provider {provider_id} not found")
        if not issubclass(provider_impl, MCPAuthProvider):
            raise ValueError(f"Provider {provider_id} is not an MCP OAuth provider")
        oauth_connect = await self.start_authorization_code_connect(
            provider_key=provider_key,
            provider_impl=cast(type[AuthorizationCodeOAuthProvider], provider_impl),
        )
        return PlatformMCPCatalogConnectResult(
            mcp_integration=existing_mcp_integration,
            oauth_connect=oauth_connect,
        )

    async def _get_mcp_integration_by_catalog(
        self, catalog: PlatformMCPCatalogEntry
    ) -> MCPIntegration | None:
        """Return this workspace's MCP row for a catalog template, if present."""
        statement = select(MCPIntegration).where(
            MCPIntegration.workspace_id == self.workspace_id,
            MCPIntegration.catalog_slug == catalog.slug,
        )
        result = await self.session.execute(statement)
        if mcp_integration := result.scalars().first():
            return mcp_integration

        # Legacy rows predate the ``catalog_slug`` column, so they carry no
        # marker. Adopt a null-slug row only when its slug matches the catalog
        # slug AND its server config matches the catalog recipe, then heal it
        # in place. The recipe check prevents a coincidentally same-named custom
        # integration from being hijacked as a platform row.
        spec = self._catalog_connection_spec(catalog)
        if spec is not None:
            legacy = (
                (
                    await self.session.execute(
                        select(MCPIntegration).where(
                            MCPIntegration.workspace_id == self.workspace_id,
                            MCPIntegration.catalog_slug.is_(None),
                            MCPIntegration.slug == catalog.slug,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if legacy is not None and self._mcp_integration_matches_catalog_recipe(
                legacy, spec
            ):
                legacy.catalog_slug = catalog.slug
                self.session.add(legacy)
                await self.session.commit()
                await self.session.refresh(legacy)
                self.logger.info(
                    "Adopted legacy MCP integration into catalog",
                    mcp_integration_id=legacy.id,
                    catalog_slug=catalog.slug,
                )
                return legacy

        provider_id = catalog.provider_id
        if not provider_id:
            return None
        provider_impl = get_provider_class(
            ProviderKey(id=provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE)
        )
        if provider_impl is None or not issubclass(provider_impl, MCPAuthProvider):
            return None
        mcp_provider_impl = cast(type[MCPAuthProvider], provider_impl)
        statement = (
            select(MCPIntegration)
            .join(
                OAuthIntegration,
                OAuthIntegration.id == MCPIntegration.oauth_integration_id,
            )
            .where(
                MCPIntegration.workspace_id == self.workspace_id,
                OAuthIntegration.provider_id == provider_id,
            )
        )
        result = await self.session.execute(statement)
        return next(
            (
                mcp_integration
                for mcp_integration in result.scalars()
                if self._mcp_integration_uses_provider_server(
                    mcp_integration,
                    mcp_provider_impl,
                )
                and self._mcp_integration_has_provider_slug(
                    mcp_integration,
                    mcp_provider_impl,
                )
            ),
            None,
        )

    @staticmethod
    def _catalog_connection_spec(
        catalog: PlatformMCPCatalogEntry,
    ) -> MCPConnectionSpec | None:
        """Return the validated runtime catalog connection spec."""
        return catalog.connection_spec

    @staticmethod
    def _catalog_requires_user_config(spec: MCPConnectionSpec) -> bool:
        """Whether direct Connect lacks enough endpoint data to create a row."""
        server_uri = spec.server_uri if spec.server_type == "http" else None
        if server_uri and _CATALOG_PLACEHOLDER_RE.search(server_uri):
            return True
        required_config_targets = {"server_uri", "oauth_client"}
        return any(
            credential.required and credential.target in required_config_targets
            for credential in spec.credentials
        ) or any(
            field.required and field.target in required_config_targets
            for field in spec.config_fields
        )

    @staticmethod
    def _catalog_stdio_command(spec: MCPConnectionSpec) -> tuple[str, list[str]]:
        """Pick a supported stdio command from a catalog spec."""
        if spec.server_type != "stdio":
            return "", []
        for package in spec.packages:
            if package.command in ALLOWED_MCP_COMMANDS:
                return package.command, package.args
        if spec.stdio_command and spec.stdio_command in ALLOWED_MCP_COMMANDS:
            return spec.stdio_command, spec.stdio_args
        return "", spec.stdio_args

    @classmethod
    def _catalog_connect_create_params(
        cls,
        *,
        catalog: PlatformMCPCatalogEntry,
        spec: MCPConnectionSpec,
    ) -> MCPIntegrationCreate:
        """Build create params for a catalog row that needs no user input."""
        if cls._catalog_requires_user_config(spec):
            raise ValueError(f"{catalog.name} requires configuration before connect")

        if spec.server_type == "http":
            if spec.auth_type == MCPAuthType.OAUTH2:
                raise ValueError(f"{catalog.name} requires OAuth before connect")
            return MCPHttpIntegrationCreate(
                name=catalog.name,
                description=catalog.description,
                timeout=30,
                catalog_slug=catalog.slug,
                server_type="http",
                server_uri=spec.server_uri,
                auth_type=spec.auth_type,
            )

        command, args = cls._catalog_stdio_command(spec)
        if not command:
            raise ValueError(f"{catalog.name} requires configuration before connect")
        return MCPStdioIntegrationCreate(
            name=catalog.name,
            description=catalog.description,
            timeout=30,
            catalog_slug=catalog.slug,
            server_type="stdio",
            stdio_command=command,
            stdio_args=args or None,
        )

    @classmethod
    def _mcp_integration_matches_catalog_recipe(
        cls, mcp_integration: MCPIntegration, spec: MCPConnectionSpec
    ) -> bool:
        """Whether a row's server config matches a catalog recipe.

        Used to adopt legacy (null ``catalog_slug``) rows: a matching server
        type and auth type plus, for http, the same host (or a placeholder
        recipe where the user supplies the host) or, for stdio, the same
        launch command. This guards against hijacking a coincidentally
        same-named custom row that points somewhere else or authenticates
        differently (e.g. a header-auth row adopted as an OAuth catalog row
        would show connected instead of prompting for OAuth setup).
        """
        if mcp_integration.server_type != spec.server_type:
            return False
        if mcp_integration.auth_type != spec.auth_type:
            return False

        if spec.server_type == "http":
            recipe_uri = spec.server_uri or ""
            # Placeholder recipes (user supplies the host) can't be matched by
            # host, so the slug + server-type agreement is the strongest signal.
            if not recipe_uri or _CATALOG_PLACEHOLDER_RE.search(recipe_uri):
                return True
            recipe_host = urlparse(recipe_uri).hostname
            row_host = urlparse(mcp_integration.server_uri or "").hostname
            return recipe_host is not None and recipe_host == row_host

        recipe_command, _ = cls._catalog_stdio_command(spec)
        if not recipe_command:
            return False
        return mcp_integration.stdio_command == recipe_command

    @staticmethod
    def _mcp_integration_uses_provider_server(
        mcp_integration: MCPIntegration, provider_impl: type[MCPAuthProvider]
    ) -> bool:
        return (
            mcp_integration.server_type == "http"
            and mcp_integration.auth_type == MCPAuthType.OAUTH2
            and mcp_integration.server_uri == provider_impl.mcp_server_uri
        )

    @staticmethod
    def _mcp_integration_has_provider_slug(
        mcp_integration: MCPIntegration, provider_impl: type[MCPAuthProvider]
    ) -> bool:
        provider_slug = provider_impl.id
        if mcp_integration.slug == provider_slug:
            return True
        suffix = mcp_integration.slug.removeprefix(f"{provider_slug}-")
        return suffix != mcp_integration.slug and suffix.isdigit()

    def _is_platform_managed_mcp_integration(
        self, mcp_integration: MCPIntegration
    ) -> bool:
        """Whether an MCP integration is owned by the MCP OAuth provider lifecycle.

        Platform-managed rows are auto-created by ``MCPAuthProvider`` flows in
        ``_auto_create_mcp_integration_if_needed`` or created from catalog
        recipes carrying a ``catalog_slug`` marker.
        """
        if mcp_integration.catalog_slug is not None:
            return True

        oauth_integration = mcp_integration.oauth_integration
        if oauth_integration is None:
            return False
        provider_impl = get_provider_class(
            ProviderKey(
                id=oauth_integration.provider_id,
                grant_type=oauth_integration.grant_type,
            )
        )
        return bool(
            provider_impl
            and issubclass(provider_impl, MCPAuthProvider)
            and self._mcp_integration_uses_provider_server(
                mcp_integration,
                cast(type[MCPAuthProvider], provider_impl),
            )
            and self._mcp_integration_has_provider_slug(
                mcp_integration,
                cast(type[MCPAuthProvider], provider_impl),
            )
        )

    async def list_mcp_integrations(
        self, *, source: MCPIntegrationSource | None = None
    ) -> Sequence[MCPIntegration]:
        """List MCP integrations for the workspace, optionally filtered by source."""
        statement = select(MCPIntegration).where(
            MCPIntegration.workspace_id == self.workspace_id
        )
        result = await self.session.execute(statement)
        integrations = result.scalars().all()
        if source is None:
            return integrations
        want_platform = source == "platform"
        return [
            mcp
            for mcp in integrations
            if self._is_platform_managed_mcp_integration(mcp) == want_platform
        ]

    @staticmethod
    def _mcp_integration_state_from_access_token(
        *,
        mcp_integration: MCPIntegration,
        encrypted_access_token: bytes | None,
    ) -> PlatformMCPCatalogState:
        if mcp_integration.auth_type != MCPAuthType.OAUTH2:
            return "connected"
        if encrypted_access_token is not None and is_set(encrypted_access_token):
            return "connected"
        return "configured"

    async def _mcp_oauth_access_tokens_by_id(
        self, mcp_integrations: Sequence[MCPIntegration]
    ) -> dict[uuid.UUID, bytes | None]:
        oauth_integration_ids = {
            oauth_integration_id
            for mcp_integration in mcp_integrations
            if mcp_integration.auth_type == MCPAuthType.OAUTH2
            and (oauth_integration_id := mcp_integration.oauth_integration_id)
            is not None
        }
        if not oauth_integration_ids:
            return {}

        result = await self.session.execute(
            select(OAuthIntegration.id, OAuthIntegration.encrypted_access_token).where(
                OAuthIntegration.workspace_id == self.workspace_id,
                OAuthIntegration.id.in_(oauth_integration_ids),
            )
        )
        rows = result.tuples().all()
        return dict(rows)

    async def mcp_integration_state(
        self, *, mcp_integration: MCPIntegration
    ) -> PlatformMCPCatalogState:
        tokens_by_id = await self._mcp_oauth_access_tokens_by_id([mcp_integration])
        oauth_integration_id = mcp_integration.oauth_integration_id
        encrypted_access_token = (
            tokens_by_id.get(oauth_integration_id)
            if oauth_integration_id is not None
            else None
        )
        return self._mcp_integration_state_from_access_token(
            mcp_integration=mcp_integration,
            encrypted_access_token=encrypted_access_token,
        )

    async def list_mcp_integrations_with_state(
        self, *, source: MCPIntegrationSource | None = None
    ) -> Sequence[MCPIntegrationWithState]:
        """List MCP integrations with OAuth-backed connection state."""
        integrations = await self.list_mcp_integrations(source=source)
        tokens_by_id = await self._mcp_oauth_access_tokens_by_id(integrations)
        items: list[MCPIntegrationWithState] = []
        for integration in integrations:
            oauth_integration_id = integration.oauth_integration_id
            encrypted_access_token = (
                tokens_by_id.get(oauth_integration_id)
                if oauth_integration_id is not None
                else None
            )
            items.append(
                MCPIntegrationWithState(
                    integration=integration,
                    state=self._mcp_integration_state_from_access_token(
                        mcp_integration=integration,
                        encrypted_access_token=encrypted_access_token,
                    ),
                )
            )
        return items

    async def get_mcp_integration(
        self, *, mcp_integration_id: uuid.UUID
    ) -> MCPIntegration | None:
        """Get an MCP integration by ID."""
        statement = select(MCPIntegration).where(
            MCPIntegration.id == mcp_integration_id,
            MCPIntegration.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    @require_scope("integration:update")
    async def update_mcp_integration(
        self, *, mcp_integration_id: uuid.UUID, params: MCPIntegrationUpdate
    ) -> MCPIntegration | None:
        """Update an MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if not mcp_integration:
            return None
        previous_auth_type = mcp_integration.auth_type
        previous_server_type = cast(MCPServerType, mcp_integration.server_type)
        target_server_type = params.server_type or previous_server_type
        server_type_changed = (
            params.server_type is not None
            and params.server_type != previous_server_type
        )
        oauth_integration_id_was_provided = (
            "oauth_integration_id" in params.model_fields_set
        )

        if target_server_type == "http":
            target_server_uri = params.server_uri or mcp_integration.server_uri
            if not target_server_uri:
                raise ValueError("server_uri is required for http-type servers")

            # Prefer the request value; on a server-type switch reset to the
            # http default; otherwise keep what's already stored.
            if params.auth_type is not None:
                target_auth_type = params.auth_type
            elif server_type_changed:
                target_auth_type = MCPAuthType.NONE
            else:
                target_auth_type = mcp_integration.auth_type

            if oauth_integration_id_was_provided:
                target_oauth_integration_id = params.oauth_integration_id
            elif server_type_changed:
                target_oauth_integration_id = None
            else:
                target_oauth_integration_id = mcp_integration.oauth_integration_id

            # Validate OAuth integration if auth_type is, or remains, oauth2.
            if target_auth_type == MCPAuthType.OAUTH2 and target_oauth_integration_id:
                oauth_integration = await self.session.get(
                    OAuthIntegration, target_oauth_integration_id
                )
                if (
                    not oauth_integration
                    or oauth_integration.workspace_id != self.workspace_id
                ):
                    raise ValueError(
                        "OAuth integration not found or does not belong to workspace"
                    )
            elif target_auth_type == MCPAuthType.OAUTH2:
                raise ValueError(
                    "oauth_integration_id is required for OAuth 2.0 authentication"
                )
        elif target_server_type == "stdio" and (
            server_type_changed
            or params.stdio_command is not None
            or params.stdio_args is not None
            or params.stdio_env is not None
        ):
            self._validate_stdio_server_config(
                command=(
                    params.stdio_command
                    if params.stdio_command is not None
                    else mcp_integration.stdio_command
                ),
                args=(
                    params.stdio_args
                    if params.stdio_args is not None
                    else mcp_integration.stdio_args
                ),
                env=params.stdio_env,
            )

        # Update fields
        if params.name is not None:
            if params.name.strip() != mcp_integration.name:
                mcp_integration.name = params.name.strip()
                mcp_integration.slug = await self._generate_mcp_integration_slug(
                    name=params.name
                )
        if params.description is not None:
            mcp_integration.description = (
                params.description.strip() if params.description else None
            )

        if params.server_type is not None:
            mcp_integration.server_type = params.server_type
            if params.server_type == "http":
                mcp_integration.stdio_command = None
                mcp_integration.stdio_args = None
                mcp_integration.encrypted_stdio_env = None
            else:
                mcp_integration.server_uri = None
                mcp_integration.auth_type = MCPAuthType.NONE
                mcp_integration.oauth_integration_id = None
                mcp_integration.encrypted_headers = None

        if target_server_type == "http" and params.server_uri is not None:
            mcp_integration.server_uri = params.server_uri.strip()
        if target_server_type == "http" and params.auth_type is not None:
            mcp_integration.auth_type = params.auth_type
        if target_server_type == "http" and oauth_integration_id_was_provided:
            mcp_integration.oauth_integration_id = params.oauth_integration_id

        # Update stdio-type server fields
        if target_server_type == "stdio" and params.stdio_command is not None:
            mcp_integration.stdio_command = (
                params.stdio_command.strip() if params.stdio_command else None
            )
        if target_server_type == "stdio" and params.stdio_args is not None:
            mcp_integration.stdio_args = params.stdio_args
        if target_server_type == "stdio" and params.stdio_env is not None:
            if params.stdio_env:
                mcp_integration.encrypted_stdio_env = self._encrypt_token(
                    orjson.dumps(params.stdio_env).decode()
                )
            else:
                # Empty dict means clear the env vars
                mcp_integration.encrypted_stdio_env = None
        if params.timeout is not None:
            mcp_integration.timeout = params.timeout

        # Handle encrypted header credentials for CUSTOM/OAUTH2 auth types.
        if target_server_type == "http" and params.custom_credentials is not None:
            custom_credentials = params.custom_credentials.get_secret_value()
            if custom_credentials:
                mcp_integration.encrypted_headers = self._encrypt_token(
                    custom_credentials
                )
            else:
                # Empty string means clear the credentials
                mcp_integration.encrypted_headers = None
        elif target_server_type == "http" and params.auth_type is not None:
            if params.auth_type == MCPAuthType.NONE:
                # NONE auth should never keep custom header credentials.
                mcp_integration.encrypted_headers = None
            elif (
                previous_auth_type == MCPAuthType.CUSTOM
                and params.auth_type == MCPAuthType.OAUTH2
            ):
                # Avoid carrying CUSTOM credentials into OAuth unless explicitly set.
                mcp_integration.encrypted_headers = None

        self.session.add(mcp_integration)
        await self.session.commit()
        await self.session.refresh(mcp_integration)

        self.logger.info(
            "Updated MCP integration",
            mcp_integration_id=mcp_integration.id,
        )

        return mcp_integration

    @require_scope("integration:delete")
    async def delete_mcp_integration(self, *, mcp_integration_id: uuid.UUID) -> bool:
        """Delete an MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if not mcp_integration:
            return False

        id_str = str(mcp_integration_id)

        try:
            pruned_preset_ids = (
                await self.session.scalars(
                    update(AgentPreset)
                    .where(
                        and_(
                            AgentPreset.workspace_id == self.workspace_id,
                            AgentPreset.mcp_integrations.isnot(None),
                            AgentPreset.mcp_integrations.contains([id_str]),
                        )
                    )
                    .values(
                        mcp_integrations=AgentPreset.mcp_integrations.op("-")(id_str)
                    )
                    .returning(AgentPreset.id)
                    .execution_options(synchronize_session="fetch")
                )
            ).all()
            await self._version_pruned_agent_presets(preset_ids=pruned_preset_ids)

            await self.session.execute(
                update(AgentSession)
                .where(
                    and_(
                        AgentSession.workspace_id == self.workspace_id,
                        AgentSession.mcp_integrations.isnot(None),
                        AgentSession.mcp_integrations.contains([id_str]),
                    )
                )
                .values(mcp_integrations=AgentSession.mcp_integrations.op("-")(id_str))
                .execution_options(synchronize_session="fetch")
            )

            # If backed by an OAuth integration, lock it to serialize deletes for shared refs.
            oauth_integration = None
            oauth_integration_id = mcp_integration.oauth_integration_id
            if oauth_integration_id:
                oauth_integration_result = await self.session.execute(
                    select(OAuthIntegration)
                    .where(
                        OAuthIntegration.id == oauth_integration_id,
                        OAuthIntegration.workspace_id == self.workspace_id,
                    )
                    .with_for_update()
                )
                oauth_integration = oauth_integration_result.scalars().first()

            await self.session.delete(mcp_integration)
            await self.session.flush()

            if oauth_integration and oauth_integration_id:
                remaining_refs_result = await self.session.execute(
                    select(MCPIntegration.id)
                    .where(
                        MCPIntegration.workspace_id == self.workspace_id,
                        MCPIntegration.oauth_integration_id == oauth_integration_id,
                    )
                    .limit(1)
                )
                has_remaining_refs = remaining_refs_result.scalars().first() is not None

                if (
                    not has_remaining_refs
                    and await self._is_mcp_lifecycle_owned_oauth_integration(
                        integration=oauth_integration
                    )
                ):
                    provider_key = ProviderKey(
                        id=oauth_integration.provider_id,
                        grant_type=oauth_integration.grant_type,
                    )
                    is_custom_provider = oauth_integration.provider_id.startswith(
                        "custom_"
                    )
                    await self.session.delete(oauth_integration)
                    if is_custom_provider:
                        custom_provider = await self.get_custom_provider(
                            provider_key=provider_key
                        )
                        if custom_provider is not None:
                            await self.session.delete(custom_provider)
                    self.logger.info(
                        "Deleted backing OAuth integration",
                        oauth_integration_id=oauth_integration_id,
                        provider_id=provider_key.id,
                    )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        self.logger.info(
            "Deleted MCP integration",
            mcp_integration_id=mcp_integration_id,
            workspace_id=self.workspace_id,
        )

        return True
