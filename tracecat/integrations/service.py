"""Service for managing user integrations with external services."""

import hashlib
import os
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import orjson
from pydantic import SecretStr
from slugify import slugify
from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from tracecat import config
from tracecat.agent.common.types import MCPHttpServerConfig, MCPStdioServerConfig
from tracecat.agent.mcp.local_runtime.runtime import discover_local_mcp_server_catalog
from tracecat.agent.mcp.local_runtime.types import (
    LocalMCPDiscoveryConfig,
    LocalMCPDiscoveryError,
)
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPreset,
    MCPIntegration,
    MCPIntegrationCatalogEntry,
    MCPIntegrationDiscoveryAttempt,
    OAuthIntegration,
    WorkspaceOAuthProvider,
)
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import UserID
from tracecat.integrations.enums import (
    MCPAuthType,
    MCPCatalogArtifactType,
    MCPDiscoveryAttemptStatus,
    MCPDiscoveryStatus,
    MCPDiscoveryTrigger,
    MCPTransport,
    OAuthGrantType,
)
from tracecat.integrations.mcp_discovery_types import (
    MCPDiscoveryWorkflowArgs,
    MCPDiscoveryWorkflowResult,
    NormalizedMCPArtifact,
)
from tracecat.integrations.mcp_templating import (
    collect_mcp_expressions,
    eval_mcp_templated_object,
)
from tracecat.integrations.mcp_validation import (
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
)
from tracecat.integrations.schemas import (
    CustomOAuthProviderCreate,
    MCPIntegrationCreate,
    MCPIntegrationUpdate,
    ProviderConfig,
    ProviderKey,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_value, encrypt_value, is_set
from tracecat.secrets.schemas import SecretSearch
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseWorkspaceService
from tracecat.variables.schemas import VariableSearch
from tracecat.variables.service import VariablesService


class InsecureOAuthEndpointError(ValueError):
    """Raised when OAuth endpoints are not secured with HTTPS."""


class IntegrationService(BaseWorkspaceService):
    """Service for managing user integrations."""

    service_name = "integrations"

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
        self, *, name: str, requested_id: str | None, grant_type: OAuthGrantType
    ) -> str:
        """Generate a unique provider identifier for a custom provider."""
        base_source = requested_id or name
        slug = slugify(base_source, separator="_") or uuid4().hex
        if not slug.startswith("custom_"):
            slug = f"custom_{slug}"

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
        self, *, params: CustomOAuthProviderCreate
    ) -> WorkspaceOAuthProvider:
        """Create a new custom OAuth provider for the workspace."""
        provider_id = await self._generate_custom_provider_id(
            name=params.name,
            requested_id=params.provider_id,
            grant_type=params.grant_type,
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
        encryption_key = (
            os.environ.get("TRACECAT__DB_ENCRYPTION_KEY")
            or config.TRACECAT__DB_ENCRYPTION_KEY
        )
        if not encryption_key:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set")
        self._encryption_key = encryption_key

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
        self._disconnect_integration_state(integration=integration)
        self.session.add(integration)
        await self.session.commit()

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
        integration.expires_at = datetime.now() + timedelta(
            seconds=token_response.expires_in
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

        # Check if MCP integration already exists for this OAuth integration
        existing_mcp = await self.session.execute(
            select(MCPIntegration).where(
                MCPIntegration.oauth_integration_id == integration.id,
                MCPIntegration.workspace_id == self.workspace_id,
            )
        )
        mcp_integration = existing_mcp.scalars().first()

        if mcp_integration is None:
            # Create new MCP integration
            metadata = provider_impl.metadata

            # Use provider ID as slug to preserve underscores for icon mapping
            slug = provider_impl.id
            if await self._mcp_integration_slug_taken(slug):
                slug = await self._generate_mcp_integration_slug(
                    name=metadata.name, requested_slug=provider_impl.id
                )

            mcp_integration = MCPIntegration(
                workspace_id=self.workspace_id,
                # Keep provider metadata display names as-is (including "MCP" suffixes).
                name=metadata.name,
                description=metadata.description,
                slug=slug,
                scope_namespace=await self._generate_mcp_scope_namespace(),
                server_uri=provider_impl.mcp_server_uri,
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
                transport=MCPTransport.HTTP.value,
                discovery_status=MCPDiscoveryStatus.PENDING.value,
                catalog_version=0,
                sandbox_allow_network=False,
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
            await self.enqueue_mcp_http_discovery(
                mcp_integration_id=mcp_integration.id,
                trigger=MCPDiscoveryTrigger.CREATE,
            )
        else:
            # Update existing MCP integration to ensure it references the OAuth integration
            if mcp_integration.oauth_integration_id != integration.id:
                mcp_integration.oauth_integration_id = integration.id
                self.session.add(mcp_integration)
                await self.session.commit()

                self.logger.info(
                    "Updated MCP integration OAuth reference",
                    mcp_integration_id=mcp_integration.id,
                    oauth_integration_id=integration.id,
                )

            if mcp_integration.server_type == "http":
                await self.enqueue_mcp_http_discovery(
                    mcp_integration_id=mcp_integration.id,
                    trigger=MCPDiscoveryTrigger.UPDATE,
                )

    async def _generate_mcp_integration_slug(
        self, *, name: str, requested_slug: str | None = None
    ) -> str:
        """Generate a unique slug for an MCP integration."""
        if requested_slug:
            # Preserve underscores for provider IDs used by icon mapping.
            slug = slugify(requested_slug, separator="_") or uuid4().hex[:8]
        else:
            slug = slugify(name, separator="-") or uuid4().hex[:8]

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
        provider_impl = await self.resolve_provider_impl(
            provider_key=ProviderKey(
                id=integration.provider_id,
                grant_type=integration.grant_type,
            )
        )
        return bool(provider_impl and issubclass(provider_impl, MCPAuthProvider))

    async def _mcp_integration_slug_taken(self, slug: str) -> bool:
        """Check if an MCP integration slug is already taken."""
        statement = select(MCPIntegration).where(
            MCPIntegration.workspace_id == self.workspace_id,
            MCPIntegration.slug == slug,
        )
        result = await self.session.execute(statement)
        return result.scalars().first() is not None

    async def _mcp_scope_namespace_taken(self, scope_namespace: str) -> bool:
        """Check if an MCP scope namespace is already taken."""
        statement = select(MCPIntegration).where(
            MCPIntegration.scope_namespace == scope_namespace
        )
        result = await self.session.execute(statement)
        return result.scalars().first() is not None

    async def _generate_mcp_scope_namespace(self) -> str:
        """Generate an immutable scope namespace for a new MCP integration."""
        while True:
            candidate = secrets.token_hex(8)
            if not await self._mcp_scope_namespace_taken(candidate):
                return candidate

    async def _get_mcp_integration_for_update(
        self, *, mcp_integration_id: uuid.UUID
    ) -> MCPIntegration | None:
        """Lock and return an MCP integration for serialized discovery writes."""
        statement = (
            select(MCPIntegration)
            .where(
                MCPIntegration.id == mcp_integration_id,
                MCPIntegration.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    @staticmethod
    def _build_mcp_artifact_key(
        *,
        artifact_type: MCPCatalogArtifactType,
        artifact_ref: str,
    ) -> str:
        """Build the stable per-integration artifact key."""
        prefix = slugify(artifact_ref, separator="-")[:48].rstrip("-")
        if not prefix:
            prefix = artifact_type.value
        digest = hashlib.sha256(
            f"{artifact_type.value}\n{artifact_ref}".encode()
        ).hexdigest()[:10]
        return f"{prefix}-{digest}"

    @staticmethod
    def _build_mcp_artifact_search_text(
        *,
        artifact_ref: str,
        display_name: str | None,
        description: str | None,
    ) -> str:
        return "\n".join(
            part for part in (display_name, artifact_ref, description) if part
        )

    @staticmethod
    def _build_mcp_artifact_counts(
        artifacts: Sequence[NormalizedMCPArtifact],
    ) -> dict[str, int]:
        counts = {artifact_type.value: 0 for artifact_type in MCPCatalogArtifactType}
        for artifact in artifacts:
            counts[artifact.artifact_type.value] += 1
        return counts

    def _decrypt_mcp_headers(
        self, *, mcp_integration: MCPIntegration
    ) -> dict[str, str] | None:
        """Decrypt and validate stored MCP custom headers."""
        if not mcp_integration.encrypted_headers:
            return None
        if not is_set(mcp_integration.encrypted_headers):
            return None
        if not (decrypted := self._decrypt_token(mcp_integration.encrypted_headers)):
            return None

        try:
            parsed_headers = orjson.loads(decrypted)
        except orjson.JSONDecodeError as err:
            self.logger.warning(
                "Failed to parse custom credentials for MCP integration",
                mcp_integration_id=mcp_integration.id,
                error=str(err),
            )
            return None

        if not isinstance(parsed_headers, dict):
            self.logger.warning(
                "Custom credentials for MCP integration must be a JSON object",
                mcp_integration_id=mcp_integration.id,
            )
            return None

        custom_headers: dict[str, str] = {}
        for key, value in parsed_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                self.logger.warning(
                    "Custom credentials for MCP integration must contain string header values",
                    mcp_integration_id=mcp_integration.id,
                )
                return None
            custom_headers[key] = value
        return custom_headers

    @require_scope("integration:read")
    async def resolve_mcp_http_server_config(
        self,
        *,
        mcp_integration: MCPIntegration,
        server_name: str | None = None,
    ) -> MCPHttpServerConfig | None:
        """Resolve a stored HTTP/SSE MCP integration into a live client config."""
        if mcp_integration.server_type != "http":
            return None
        if not mcp_integration.server_uri:
            self.logger.warning(
                "HTTP-type MCP integration has no server URI",
                mcp_integration_id=mcp_integration.id,
            )
            return None

        headers: dict[str, str] = {}

        match mcp_integration.auth_type:
            case MCPAuthType.OAUTH2:
                oauth_integration_id = mcp_integration.oauth_integration_id
                if oauth_integration_id is None:
                    self.logger.warning(
                        "OAUTH2 MCP integration has no linked OAuth integration",
                        mcp_integration_id=mcp_integration.id,
                    )
                    return None

                stmt = select(OAuthIntegration).where(
                    OAuthIntegration.id == oauth_integration_id,
                    OAuthIntegration.workspace_id == self.workspace_id,
                )
                result = await self.session.execute(stmt)
                oauth_integration = result.scalars().first()
                if oauth_integration is None:
                    self.logger.warning(
                        "OAuth integration not found for MCP integration",
                        mcp_integration_id=mcp_integration.id,
                        oauth_integration_id=oauth_integration_id,
                    )
                    return None

                await self.refresh_token_if_needed(oauth_integration)
                access_token = await self.get_access_token(oauth_integration)
                if access_token is None:
                    self.logger.warning(
                        "No access token available for MCP integration",
                        mcp_integration_id=mcp_integration.id,
                        oauth_integration_id=oauth_integration_id,
                    )
                    return None

                token_type = oauth_integration.token_type or "Bearer"
                headers["Authorization"] = (
                    f"{token_type} {access_token.get_secret_value()}"
                )

                custom_headers = self._decrypt_mcp_headers(
                    mcp_integration=mcp_integration
                )
                if custom_headers is None:
                    custom_headers = {}

                auth_header_keys = [
                    key
                    for key in custom_headers
                    if key.strip().casefold() == "authorization"
                ]
                for auth_header_key in auth_header_keys:
                    custom_headers.pop(auth_header_key, None)
                headers.update(custom_headers)

            case MCPAuthType.CUSTOM:
                custom_headers = self._decrypt_mcp_headers(
                    mcp_integration=mcp_integration
                )
                if custom_headers is None:
                    self.logger.warning(
                        "CUSTOM MCP integration has invalid stored credentials",
                        mcp_integration_id=mcp_integration.id,
                    )
                    return None
                headers.update(custom_headers)

            case MCPAuthType.NONE:
                pass

            case _:
                self.logger.warning(
                    "Unknown auth type for MCP integration",
                    mcp_integration_id=mcp_integration.id,
                    auth_type=str(mcp_integration.auth_type),
                )
                return None

        http_config: MCPHttpServerConfig = {
            "type": "http",
            "name": server_name or mcp_integration.name,
            "url": mcp_integration.server_uri,
            "transport": MCPTransport(mcp_integration.transport).value,
            "headers": headers,
        }
        if mcp_integration.timeout is not None:
            http_config["timeout"] = mcp_integration.timeout
        return http_config

    @require_scope("integration:read")
    async def resolve_mcp_stdio_env(
        self,
        *,
        stdio_env: dict[str, str],
        mcp_integration_id: uuid.UUID,
        mcp_integration_slug: str,
    ) -> dict[str, str]:
        """Resolve template expressions in stdio env using workspace secrets/vars."""
        collected = collect_mcp_expressions(stdio_env)
        if not collected.secrets and not collected.variables:
            return stdio_env

        async with SecretsService.with_session(role=self.role) as secrets_service:
            secret_models = await secrets_service.search_secrets(
                SecretSearch(
                    names=collected.secrets,
                    environment=DEFAULT_SECRETS_ENVIRONMENT,
                )
            )
            secrets = {
                secret.name: {
                    key.key: key.value.get_secret_value()
                    for key in secrets_service.decrypt_keys(secret.encrypted_keys)
                }
                for secret in secret_models
            }
        async with VariablesService.with_session(role=self.role) as variables_service:
            variables = await variables_service.search_variables(
                VariableSearch(names=collected.variables)
            )
        vars_map = {variable.name: variable.values for variable in variables}

        context: dict[str, Any] = {
            "SECRETS": secrets,
            "VARS": vars_map,
        }
        resolved = eval_mcp_templated_object(stdio_env, operand=context)
        if not isinstance(resolved, dict):
            raise TracecatValidationError(
                "Resolved stdio_env must be a JSON object with string values"
            )

        non_string_values = [
            key for key, value in resolved.items() if not isinstance(value, str)
        ]
        if non_string_values:
            raise TracecatValidationError(
                "Resolved stdio_env values must be strings "
                f"(invalid keys: {sorted(non_string_values)})"
            )

        self.logger.info(
            "Resolved stdio_env template expressions",
            workspace_id=self.workspace_id,
            mcp_integration_id=mcp_integration_id,
            mcp_integration_slug=mcp_integration_slug,
            env_key_count=len(resolved),
            secret_ref_count=len(collected.secrets),
            var_ref_count=len(collected.variables),
        )
        return cast(dict[str, str], resolved)

    @require_scope("integration:read")
    async def resolve_mcp_stdio_server_config(
        self,
        *,
        mcp_integration: MCPIntegration,
        server_name: str | None = None,
    ) -> MCPStdioServerConfig | None:
        """Resolve a stored stdio MCP integration into a live client config."""
        if mcp_integration.server_type != "stdio":
            return None
        if not mcp_integration.stdio_command:
            self.logger.warning(
                "Stdio-type MCP integration has no command",
                mcp_integration_id=mcp_integration.id,
            )
            return None

        stdio_env = self.decrypt_stdio_env(mcp_integration)
        if stdio_env:
            try:
                stdio_env = await self.resolve_mcp_stdio_env(
                    stdio_env=stdio_env,
                    mcp_integration_id=mcp_integration.id,
                    mcp_integration_slug=mcp_integration.slug,
                )
            except Exception as exc:
                self.logger.warning(
                    "Failed to resolve stdio env for MCP integration",
                    mcp_integration_id=mcp_integration.id,
                    error=str(exc),
                )
                return None

        try:
            validate_mcp_command_config(
                command=mcp_integration.stdio_command,
                args=mcp_integration.stdio_args,
                env=stdio_env,
                name=mcp_integration.slug,
            )
        except MCPValidationError as exc:
            self.logger.warning(
                "Stdio-type MCP integration failed validation",
                mcp_integration_id=mcp_integration.id,
                error=str(exc),
            )
            return None

        stdio_config: MCPStdioServerConfig = {
            "type": "stdio",
            "name": server_name or mcp_integration.scope_namespace,
            "command": mcp_integration.stdio_command,
        }
        if mcp_integration.stdio_args:
            stdio_config["args"] = mcp_integration.stdio_args
        if stdio_env:
            stdio_config["env"] = stdio_env
        if mcp_integration.timeout is not None:
            stdio_config["timeout"] = mcp_integration.timeout
        return stdio_config

    async def _integration_has_active_catalog(
        self, *, mcp_integration_id: uuid.UUID
    ) -> bool:
        statement = select(MCPIntegrationCatalogEntry.id).where(
            MCPIntegrationCatalogEntry.workspace_id == self.workspace_id,
            MCPIntegrationCatalogEntry.mcp_integration_id == mcp_integration_id,
            MCPIntegrationCatalogEntry.is_active.is_(True),
        )
        result = await self.session.execute(statement.limit(1))
        return result.scalar_one_or_none() is not None

    async def _insert_mcp_discovery_attempt(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
        status: MCPDiscoveryAttemptStatus,
        started_at: datetime,
        finished_at: datetime,
        catalog_version: int | None = None,
        artifact_counts: dict[str, int] | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        error_details: dict[str, Any] | None = None,
    ) -> None:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        self.session.add(
            MCPIntegrationDiscoveryAttempt(
                mcp_integration_id=mcp_integration_id,
                workspace_id=self.workspace_id,
                trigger=trigger.value,
                status=status.value,
                catalog_version=catalog_version,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                artifact_counts=artifact_counts,
                error_code=error_code,
                error_summary=error_summary,
                error_details=error_details,
            )
        )

    async def persist_mcp_discovery_success(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
        started_at: datetime,
        finished_at: datetime,
        artifacts: Sequence[NormalizedMCPArtifact],
    ) -> MCPIntegration:
        """Persist a successful MCP discovery run."""
        mcp_integration = await self._get_mcp_integration_for_update(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")

        artifact_counts = self._build_mcp_artifact_counts(artifacts)
        if (
            mcp_integration.last_discovery_attempt_at is not None
            and mcp_integration.last_discovery_attempt_at > started_at
        ):
            await self._insert_mcp_discovery_attempt(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                status=MCPDiscoveryAttemptStatus.SUCCEEDED,
                started_at=started_at,
                finished_at=finished_at,
                catalog_version=None,
                artifact_counts=artifact_counts,
            )
            await self.session.commit()
            await self.session.refresh(mcp_integration)
            return mcp_integration

        catalog_version = mcp_integration.catalog_version + 1
        upsert_rows: list[dict[str, Any]] = []
        active_pairs: list[tuple[str, str]] = []
        for artifact in artifacts:
            artifact_key = self._build_mcp_artifact_key(
                artifact_type=artifact.artifact_type,
                artifact_ref=artifact.artifact_ref,
            )
            active_pairs.append((artifact.artifact_type.value, artifact_key))
            upsert_rows.append(
                {
                    "mcp_integration_id": mcp_integration_id,
                    "workspace_id": self.workspace_id,
                    "integration_name": mcp_integration.name,
                    "artifact_type": artifact.artifact_type.value,
                    "artifact_key": artifact_key,
                    "artifact_ref": artifact.artifact_ref,
                    "display_name": artifact.display_name,
                    "description": artifact.description,
                    "input_schema": artifact.input_schema,
                    "metadata": artifact.metadata,
                    "raw_payload": artifact.raw_payload,
                    "content_hash": artifact.content_hash,
                    "is_active": True,
                    "search_vector": func.to_tsvector(
                        "simple",
                        self._build_mcp_artifact_search_text(
                            artifact_ref=artifact.artifact_ref,
                            display_name=artifact.display_name,
                            description=artifact.description,
                        ),
                    ),
                }
            )

        if upsert_rows:
            catalog_table = cast(Any, MCPIntegrationCatalogEntry.__table__)
            upsert_stmt = insert(catalog_table).values(upsert_rows)
            metadata_column = catalog_table.c["metadata"]
            await self.session.execute(
                upsert_stmt.on_conflict_do_update(
                    index_elements=[
                        catalog_table.c.mcp_integration_id,
                        catalog_table.c.artifact_type,
                        catalog_table.c.artifact_key,
                    ],
                    set_={
                        "workspace_id": upsert_stmt.excluded.workspace_id,
                        "integration_name": upsert_stmt.excluded.integration_name,
                        "artifact_ref": upsert_stmt.excluded.artifact_ref,
                        "display_name": upsert_stmt.excluded.display_name,
                        "description": upsert_stmt.excluded.description,
                        "input_schema": upsert_stmt.excluded.input_schema,
                        metadata_column: upsert_stmt.excluded["metadata"],
                        "raw_payload": upsert_stmt.excluded.raw_payload,
                        "content_hash": upsert_stmt.excluded.content_hash,
                        "is_active": True,
                        "search_vector": upsert_stmt.excluded.search_vector,
                        "updated_at": func.now(),
                    },
                )
            )

        deactivate_stmt = (
            update(MCPIntegrationCatalogEntry)
            .where(
                MCPIntegrationCatalogEntry.workspace_id == self.workspace_id,
                MCPIntegrationCatalogEntry.mcp_integration_id == mcp_integration_id,
                MCPIntegrationCatalogEntry.is_active.is_(True),
            )
            .values(is_active=False, updated_at=func.now())
        )
        if active_pairs:
            deactivate_stmt = deactivate_stmt.where(
                tuple_(
                    MCPIntegrationCatalogEntry.artifact_type,
                    MCPIntegrationCatalogEntry.artifact_key,
                ).not_in(active_pairs)
            )
        await self.session.execute(deactivate_stmt)

        mcp_integration.catalog_version = catalog_version
        mcp_integration.discovery_status = MCPDiscoveryStatus.SUCCEEDED.value
        mcp_integration.last_discovered_at = finished_at
        mcp_integration.last_discovery_error_code = None
        mcp_integration.last_discovery_error_summary = None
        self.session.add(mcp_integration)
        await self._insert_mcp_discovery_attempt(
            mcp_integration_id=mcp_integration_id,
            trigger=trigger,
            status=MCPDiscoveryAttemptStatus.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            catalog_version=catalog_version,
            artifact_counts=artifact_counts,
        )
        await self.session.commit()
        await self.session.refresh(mcp_integration)
        return mcp_integration

    def _redact_mcp_discovery_error(
        self,
        *,
        exc: Exception,
        has_active_catalog: bool,
    ) -> tuple[str, str, MCPDiscoveryStatus, dict[str, Any]]:
        """Map internal discovery failures to user-safe persisted state."""
        discovery_status = (
            MCPDiscoveryStatus.STALE
            if has_active_catalog
            else MCPDiscoveryStatus.FAILED
        )
        details: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

        if isinstance(exc, LocalMCPDiscoveryError):
            details.update(exc.details)
            return (
                exc.phase.value,
                exc.summary,
                discovery_status,
                details,
            )
        if isinstance(exc, httpx.TimeoutException):
            return (
                "timeout",
                "Timed out while discovering MCP artifacts.",
                discovery_status,
                details,
            )
        if isinstance(exc, httpx.HTTPError):
            return (
                "connection_error",
                "Could not connect to the MCP server.",
                discovery_status,
                details,
            )
        if isinstance(exc, ValueError):
            return (
                "invalid_config",
                "The MCP integration configuration is incomplete.",
                discovery_status,
                details,
            )
        return (
            "unexpected_error",
            "Unexpected error during MCP discovery.",
            discovery_status,
            details,
        )

    async def persist_mcp_discovery_failure(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
        started_at: datetime,
        finished_at: datetime,
        exc: Exception,
    ) -> MCPIntegration:
        """Persist a failed MCP discovery run without touching active rows."""
        mcp_integration = await self._get_mcp_integration_for_update(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")

        has_active_catalog = await self._integration_has_active_catalog(
            mcp_integration_id=mcp_integration_id
        )
        error_code, error_summary, discovery_status, error_details = (
            self._redact_mcp_discovery_error(
                exc=exc, has_active_catalog=has_active_catalog
            )
        )

        if (
            mcp_integration.last_discovery_attempt_at is None
            or mcp_integration.last_discovery_attempt_at <= started_at
        ):
            mcp_integration.discovery_status = discovery_status.value
            mcp_integration.last_discovery_error_code = error_code
            mcp_integration.last_discovery_error_summary = error_summary
            self.session.add(mcp_integration)

        await self._insert_mcp_discovery_attempt(
            mcp_integration_id=mcp_integration_id,
            trigger=trigger,
            status=MCPDiscoveryAttemptStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            catalog_version=None,
            error_code=error_code,
            error_summary=error_summary,
            error_details=error_details,
        )
        await self.session.commit()
        await self.session.refresh(mcp_integration)
        return mcp_integration

    async def enqueue_mcp_http_discovery(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
    ) -> MCPIntegration:
        """Schedule persisted discovery for a remote HTTP/SSE MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")
        if mcp_integration.server_type != "http":
            raise ValueError("Only HTTP/SSE MCP integrations support remote discovery")

        started_at = datetime.now(UTC)
        mcp_integration.discovery_status = MCPDiscoveryStatus.PENDING.value
        mcp_integration.last_discovery_attempt_at = started_at
        self.session.add(mcp_integration)
        await self.session.commit()
        await self.session.refresh(mcp_integration)

        workflow_args = MCPDiscoveryWorkflowArgs(
            role=self.role,
            mcp_integration_id=mcp_integration_id,
            trigger=trigger,
            started_at=started_at,
        )
        workflow_id = f"mcp-remote-discovery-{mcp_integration_id}-{started_at.strftime('%Y%m%d%H%M%S%f')}"

        try:
            client = await get_temporal_client()
            await client.start_workflow(
                "mcp_remote_discovery",
                workflow_args,
                id=workflow_id,
                task_queue=config.TRACECAT__AGENT_QUEUE,
                execution_timeout=timedelta(minutes=10),
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to enqueue remote MCP discovery",
                mcp_integration_id=mcp_integration_id,
                trigger=trigger.value,
                error=str(exc),
            )
            return await self.persist_mcp_discovery_failure(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exc=exc,
            )

        return mcp_integration

    async def enqueue_mcp_local_discovery(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
    ) -> MCPIntegration:
        """Schedule persisted discovery for a local stdio MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")
        if mcp_integration.server_type != "stdio":
            raise ValueError("Only stdio MCP integrations support local discovery")

        started_at = datetime.now(UTC)
        mcp_integration.discovery_status = MCPDiscoveryStatus.PENDING.value
        mcp_integration.last_discovery_attempt_at = started_at
        self.session.add(mcp_integration)
        await self.session.commit()
        await self.session.refresh(mcp_integration)

        workflow_args = MCPDiscoveryWorkflowArgs(
            role=self.role,
            mcp_integration_id=mcp_integration_id,
            trigger=trigger,
            started_at=started_at,
        )
        workflow_id = f"mcp-local-discovery-{mcp_integration_id}-{started_at.strftime('%Y%m%d%H%M%S%f')}"

        try:
            client = await get_temporal_client()
            await client.start_workflow(
                "mcp_local_stdio_discovery",
                workflow_args,
                id=workflow_id,
                task_queue=config.TRACECAT__MCP_QUEUE,
                execution_timeout=timedelta(minutes=10),
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to enqueue local MCP discovery",
                mcp_integration_id=mcp_integration_id,
                trigger=trigger.value,
                error=str(exc),
            )
            return await self.persist_mcp_discovery_failure(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exc=exc,
            )

        return mcp_integration

    async def enqueue_mcp_discovery(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
    ) -> MCPIntegration:
        """Schedule persisted discovery based on MCP integration server type."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")
        if mcp_integration.server_type == "http":
            return await self.enqueue_mcp_http_discovery(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
            )
        return await self.enqueue_mcp_local_discovery(
            mcp_integration_id=mcp_integration_id,
            trigger=trigger,
        )

    @require_scope("integration:update")
    async def refresh_mcp_integration_discovery(
        self, *, mcp_integration_id: uuid.UUID
    ) -> MCPIntegration | None:
        """Enqueue a persisted discovery refresh for an MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            return None
        return await self.enqueue_mcp_discovery(
            mcp_integration_id=mcp_integration_id,
            trigger=MCPDiscoveryTrigger.REFRESH,
        )

    async def run_remote_mcp_discovery(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
        started_at: datetime,
    ) -> MCPDiscoveryWorkflowResult:
        """Discover and persist the catalog for a remote HTTP/SSE MCP integration."""
        from tracecat.agent.mcp import user_client

        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")
        try:
            http_config = await self.resolve_mcp_http_server_config(
                mcp_integration=mcp_integration,
                server_name=mcp_integration.scope_namespace,
            )
            if http_config is None:
                raise ValueError("MCP integration could not be resolved for discovery")
            raw_catalog: object = await user_client.discover_mcp_server_catalog(
                http_config
            )
            if isinstance(raw_catalog, dict):
                raw_artifacts = raw_catalog.get("artifacts", [])
            else:
                raw_artifacts = cast("Sequence[object]", raw_catalog.artifacts)

            artifacts = [
                artifact
                if isinstance(artifact, NormalizedMCPArtifact)
                else NormalizedMCPArtifact.model_validate(artifact)
                for artifact in raw_artifacts
            ]
            updated = await self.persist_mcp_discovery_success(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                artifacts=artifacts,
            )
            return MCPDiscoveryWorkflowResult(
                mcp_integration_id=mcp_integration_id,
                status=updated.discovery_status,
                catalog_version=updated.catalog_version,
            )
        except Exception as exc:
            updated = await self.persist_mcp_discovery_failure(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exc=exc,
            )
            return MCPDiscoveryWorkflowResult(
                mcp_integration_id=mcp_integration_id,
                status=updated.discovery_status,
                catalog_version=updated.catalog_version,
                error_code=updated.last_discovery_error_code,
                error_summary=updated.last_discovery_error_summary,
            )

    async def run_local_mcp_discovery(
        self,
        *,
        mcp_integration_id: uuid.UUID,
        trigger: MCPDiscoveryTrigger,
        started_at: datetime,
    ) -> MCPDiscoveryWorkflowResult:
        """Discover and persist the catalog for a local stdio MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if mcp_integration is None:
            raise ValueError("MCP integration not found")

        stdio_config = await self.resolve_mcp_stdio_server_config(
            mcp_integration=mcp_integration,
            server_name=mcp_integration.scope_namespace,
        )
        if stdio_config is None:
            updated = await self.persist_mcp_discovery_failure(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exc=ValueError("MCP integration could not be resolved for discovery"),
            )
            return MCPDiscoveryWorkflowResult(
                mcp_integration_id=mcp_integration_id,
                status=updated.discovery_status,
                catalog_version=updated.catalog_version,
                error_code=updated.last_discovery_error_code,
                error_summary=updated.last_discovery_error_summary,
            )

        try:
            catalog = await discover_local_mcp_server_catalog(
                LocalMCPDiscoveryConfig(
                    organization_id=str(self.organization_id),
                    server=stdio_config,
                    sandbox_cache_dir=config.TRACECAT__MCP_SANDBOX_CACHE_DIR,
                    allow_network=mcp_integration.sandbox_allow_network,
                    egress_allowlist=tuple(
                        mcp_integration.sandbox_egress_allowlist or ()
                    ),
                    egress_denylist=tuple(
                        mcp_integration.sandbox_egress_denylist or ()
                    ),
                    timeout_seconds=mcp_integration.timeout,
                )
            )
            artifacts = [
                NormalizedMCPArtifact.model_validate(artifact)
                for artifact in catalog.artifacts
            ]
            updated = await self.persist_mcp_discovery_success(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                artifacts=artifacts,
            )
            return MCPDiscoveryWorkflowResult(
                mcp_integration_id=mcp_integration_id,
                status=updated.discovery_status,
                catalog_version=updated.catalog_version,
            )
        except Exception as exc:
            updated = await self.persist_mcp_discovery_failure(
                mcp_integration_id=mcp_integration_id,
                trigger=trigger,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                exc=exc,
            )
            return MCPDiscoveryWorkflowResult(
                mcp_integration_id=mcp_integration_id,
                status=updated.discovery_status,
                catalog_version=updated.catalog_version,
                error_code=updated.last_discovery_error_code,
                error_summary=updated.last_discovery_error_summary,
            )

    @require_scope("integration:create")
    async def create_mcp_integration(
        self, *, params: MCPIntegrationCreate
    ) -> MCPIntegration:
        """Create a new MCP integration."""
        slug = await self._generate_mcp_integration_slug(name=params.name)

        # Normalize server-type specific fields using discriminator narrowing.
        server_uri: str | None = None
        auth_type = MCPAuthType.NONE
        oauth_integration_id: uuid.UUID | None = None
        encrypted_custom_credentials: bytes | None = None
        stdio_command: str | None = None
        stdio_args: list[str] | None = None
        encrypted_stdio_env: bytes | None = None
        transport = MCPTransport.HTTP

        if params.server_type == "http":
            # Validate OAuth integration if auth_type is oauth2
            if params.auth_type == MCPAuthType.OAUTH2:
                if not params.oauth_integration_id:
                    raise ValueError(
                        "oauth_integration_id is required for OAuth 2.0 authentication"
                    )
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
            transport = params.transport
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
            scope_namespace=await self._generate_mcp_scope_namespace(),
            server_uri=server_uri,
            auth_type=auth_type,
            oauth_integration_id=oauth_integration_id,
            encrypted_headers=encrypted_custom_credentials,  # Reuse field for custom credentials
            server_type=params.server_type,
            transport=transport.value,
            stdio_command=stdio_command,
            stdio_args=stdio_args,
            encrypted_stdio_env=encrypted_stdio_env,
            timeout=params.timeout,
            discovery_status=MCPDiscoveryStatus.PENDING.value,
            catalog_version=0,
            sandbox_allow_network=False,
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

        return await self.enqueue_mcp_discovery(
            mcp_integration_id=mcp_integration.id,
            trigger=MCPDiscoveryTrigger.CREATE,
        )

    async def list_mcp_integrations(self) -> Sequence[MCPIntegration]:
        """List all MCP integrations for the workspace."""
        statement = select(MCPIntegration).where(
            MCPIntegration.workspace_id == self.workspace_id
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

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

    async def get_mcp_catalog_counts(
        self, *, mcp_integration_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict[MCPCatalogArtifactType, int]]:
        """Return active catalog counts grouped by integration and artifact type."""
        if not mcp_integration_ids:
            return {}

        statement = (
            select(
                MCPIntegrationCatalogEntry.mcp_integration_id,
                MCPIntegrationCatalogEntry.artifact_type,
                func.count(MCPIntegrationCatalogEntry.id),
            )
            .where(
                MCPIntegrationCatalogEntry.workspace_id == self.workspace_id,
                MCPIntegrationCatalogEntry.is_active.is_(True),
                MCPIntegrationCatalogEntry.mcp_integration_id.in_(
                    list(mcp_integration_ids)
                ),
            )
            .group_by(
                MCPIntegrationCatalogEntry.mcp_integration_id,
                MCPIntegrationCatalogEntry.artifact_type,
            )
        )
        result = await self.session.execute(statement)
        counts_by_integration: dict[uuid.UUID, dict[MCPCatalogArtifactType, int]] = {}
        for integration_id, artifact_type, count in result.tuples().all():
            by_type = counts_by_integration.setdefault(integration_id, {})
            by_type[MCPCatalogArtifactType(artifact_type)] = count
        return counts_by_integration

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

        # Validate OAuth integration if auth_type is being changed to oauth2
        if params.auth_type == MCPAuthType.OAUTH2:
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
            elif mcp_integration.auth_type != MCPAuthType.OAUTH2:
                raise ValueError(
                    "oauth_integration_id is required for OAuth 2.0 authentication"
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
        if params.server_uri is not None:
            mcp_integration.server_uri = params.server_uri.strip()
        if params.transport is not None and mcp_integration.server_type == "http":
            mcp_integration.transport = params.transport.value
        if params.auth_type is not None:
            mcp_integration.auth_type = params.auth_type
        if params.oauth_integration_id is not None:
            mcp_integration.oauth_integration_id = params.oauth_integration_id

        if mcp_integration.server_type == "stdio" and (
            params.stdio_command is not None
            or params.stdio_args is not None
            or params.stdio_env is not None
        ):
            self._validate_stdio_server_config(
                command=(
                    params.stdio_command
                    if params.stdio_command is not None
                    else mcp_integration.stdio_command
                ),
                args=params.stdio_args,
                env=params.stdio_env,
            )

        # Update stdio-type server fields
        if params.stdio_command is not None:
            mcp_integration.stdio_command = (
                params.stdio_command.strip() if params.stdio_command else None
            )
        if params.stdio_args is not None:
            mcp_integration.stdio_args = params.stdio_args
        if params.stdio_env is not None:
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
        if params.custom_credentials is not None:
            custom_credentials = params.custom_credentials.get_secret_value()
            if custom_credentials:
                mcp_integration.encrypted_headers = self._encrypt_token(
                    custom_credentials
                )
            else:
                # Empty string means clear the credentials
                mcp_integration.encrypted_headers = None
        elif params.auth_type is not None:
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

        return await self.enqueue_mcp_discovery(
            mcp_integration_id=mcp_integration.id,
            trigger=MCPDiscoveryTrigger.UPDATE,
        )

    @require_scope("integration:delete")
    async def delete_mcp_integration(self, *, mcp_integration_id: uuid.UUID) -> bool:
        """Delete an MCP integration."""
        mcp_integration = await self.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        if not mcp_integration:
            return False

        id_str = str(mcp_integration_id)

        # Remove stale ID references from agent presets in this workspace
        await self.session.execute(
            update(AgentPreset)
            .where(
                and_(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.mcp_integrations.isnot(None),
                    AgentPreset.mcp_integrations.contains([id_str]),
                )
            )
            .values(mcp_integrations=AgentPreset.mcp_integrations.op("-")(id_str))
        )

        try:
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
                    self._disconnect_integration_state(integration=oauth_integration)
                    self.session.add(oauth_integration)
                    self.logger.info(
                        "Disconnected backing OAuth integration",
                        oauth_integration_id=oauth_integration_id,
                        provider_id=oauth_integration.provider_id,
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
