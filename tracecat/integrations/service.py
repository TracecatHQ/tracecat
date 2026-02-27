"""Service for managing user integrations with external services."""

import os
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import cast
from urllib.parse import urlparse
from uuid import uuid4

import orjson
from pydantic import SecretStr
from slugify import slugify
from sqlalchemy import and_, or_, select, update

from tracecat import config
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPreset,
    MCPIntegration,
    OAuthIntegration,
    WorkspaceOAuthProvider,
)
from tracecat.identifiers import UserID
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
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
from tracecat.secrets.encryption import decrypt_value, encrypt_value, is_set
from tracecat.service import BaseWorkspaceService


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
        return orjson.loads(decrypted)

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
                server_uri=provider_impl.mcp_server_uri,
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
            auth_type = params.auth_type
            oauth_integration_id = params.oauth_integration_id
            if params.auth_type == MCPAuthType.CUSTOM and params.custom_credentials:
                encrypted_custom_credentials = self._encrypt_token(
                    params.custom_credentials.get_secret_value()
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

        # Handle custom credentials encryption/update (for CUSTOM auth type)
        if params.custom_credentials is not None:
            if params.custom_credentials.get_secret_value():
                mcp_integration.encrypted_headers = self._encrypt_token(
                    params.custom_credentials.get_secret_value()
                )
            else:
                # Empty string means clear the credentials
                mcp_integration.encrypted_headers = None
        elif params.auth_type is not None and params.auth_type != MCPAuthType.CUSTOM:
            # If changing away from CUSTOM, clear the credentials
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
