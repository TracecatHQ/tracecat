"""Service for managing user integrations with external services."""

import os
from datetime import datetime, timedelta
from typing import Any

from pydantic import SecretStr
from sqlmodel import col, select

from tracecat.db.schemas import OAuthIntegration
from tracecat.identifiers import UserID
from tracecat.integrations.base import (
    AuthorizationCodeOAuthProvider,
    BaseOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import ProviderConfig
from tracecat.integrations.providers import ProviderRegistry
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseWorkspaceService


class IntegrationService(BaseWorkspaceService):
    """Service for managing user integrations."""

    service_name = "integrations"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self._encryption_key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        except KeyError as e:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set") from e

    async def get_integration(
        self,
        *,
        provider_id: str,
        user_id: UserID | None = None,
    ) -> OAuthIntegration | None:
        """Get a user's integration for a specific provider."""
        statement = select(OAuthIntegration).where(
            OAuthIntegration.owner_id == self.workspace_id,
            OAuthIntegration.provider_id == provider_id,
        )
        if user_id is not None:
            statement = statement.where(OAuthIntegration.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def list_integrations(
        self, *, providers: set[str] | None = None
    ) -> list[OAuthIntegration]:
        """List all integrations for a workspace, optionally filtered by providers."""
        statement = select(OAuthIntegration).where(
            OAuthIntegration.owner_id == self.workspace_id
        )
        if providers:
            statement = statement.where(
                col(OAuthIntegration.provider_id).in_(providers)
            )
        result = await self.session.exec(statement)
        return list(result.all())

    async def store_integration(
        self,
        *,
        provider_id: str,
        user_id: UserID | None = None,
        access_token: SecretStr,
        refresh_token: SecretStr | None = None,
        expires_in: int | None = None,
        scope: str | None = None,
        provider_config: dict[str, Any] | None = None,
    ) -> OAuthIntegration:
        """Store or update a user's integration."""
        # Calculate expiration time if expires_in is provided
        expires_at = None
        if expires_in is not None:
            expires_at = datetime.now() + timedelta(seconds=expires_in)

        # Check if integration already exists

        if integration := await self.get_integration(provider_id=provider_id):
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
            if provider_config:
                # Update the provider_config field
                integration.provider_config = provider_config

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Updated user integration",
                user_id=user_id,
                provider=provider_id,
            )
            return integration
        else:
            # Create new integration
            integration = OAuthIntegration(
                owner_id=self.workspace_id,
                user_id=user_id,
                provider_id=provider_id,
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
                provider_config=provider_config or {},
            )

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Created user integration",
                user_id=user_id,
                provider=provider_id,
            )
            return integration

    async def disconnect_integration(self, *, integration: OAuthIntegration) -> None:
        """Disconnect a user's integration for a specific provider."""
        # Wipe all tokens
        integration.encrypted_access_token = b""
        integration.encrypted_refresh_token = None
        integration.expires_at = None
        integration.scope = None  # Granted scopes
        integration.requested_scopes = None
        self.session.add(integration)
        await self.session.commit()

    async def remove_integration(self, *, integration: OAuthIntegration) -> None:
        """Remove a user's integration for a specific provider."""
        await self.session.delete(integration)
        await self.session.commit()

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
        registry = ProviderRegistry.get()
        provider_impl = registry.get_class(integration.provider_id)
        if not provider_impl:
            self.logger.error(
                "Provider not found in registry",
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

            if not client_id or not client_secret:
                self.logger.warning(
                    "No client credentials found",
                    user_id=integration.user_id,
                    provider=integration.provider_id,
                )
                return None
            # Create provider config
            provider_config = ProviderConfig(
                client_id=client_id,
                client_secret=SecretStr(client_secret),
                provider_config=integration.provider_config,
                scopes=self.parse_scopes(integration.requested_scopes),
            )
            return provider_impl.from_config(provider_config)
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

    async def get_access_token(self, integration: OAuthIntegration) -> SecretStr:
        """Get the decrypted access token for an integration."""
        access_token = self._decrypt_token(integration.encrypted_access_token)
        return SecretStr(access_token)

    def get_decrypted_tokens(
        self, integration: OAuthIntegration
    ) -> tuple[str, str | None]:
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

    def _decrypt_token(self, encrypted_token: bytes) -> str:
        """Decrypt a token using the service's encryption key."""
        return decrypt_value(encrypted_token, key=self._encryption_key).decode("utf-8")

    def encrypt_client_credential(self, credential: str) -> bytes:
        """Encrypt a client credential using the service's encryption key."""
        return encrypt_value(credential.encode("utf-8"), key=self._encryption_key)

    def decrypt_client_credential(self, encrypted_credential: bytes) -> str:
        """Decrypt a client credential using the service's encryption key."""
        return decrypt_value(encrypted_credential, key=self._encryption_key).decode(
            "utf-8"
        )

    async def store_provider_config(
        self,
        *,
        provider_id: str,
        client_id: str | None = None,
        client_secret: SecretStr | None = None,
        provider_config: dict[str, Any] | None = None,
        requested_scopes: list[str] | None = None,
        grant_type: OAuthGrantType | None = None,
    ) -> OAuthIntegration:
        """Store or update provider configuration (client credentials) for a workspace."""
        # Check if integration configuration already exists for this provider

        if integration := await self.get_integration(provider_id=provider_id):
            # Update existing integration with client credentials (patch operation)
            if not any(
                (
                    client_id,
                    client_secret,
                    provider_config,
                    requested_scopes,
                    grant_type,
                )
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

            if provider_config is not None:
                integration.provider_config = provider_config

            if requested_scopes is not None:
                integration.requested_scopes = " ".join(requested_scopes)

            if grant_type is not None:
                integration.grant_type = grant_type

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Updated provider configuration",
                provider=provider_id,
                workspace_id=self.workspace_id,
            )

            return integration
        else:
            # Create new integration record with just client credentials
            # Access tokens will be added later during OAuth flow
            integration = OAuthIntegration(
                owner_id=self.workspace_id,
                provider_id=provider_id,
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
                provider_config=provider_config or {},
                requested_scopes=" ".join(requested_scopes)
                if requested_scopes
                else None,
                grant_type=grant_type
                if grant_type
                else OAuthGrantType.AUTHORIZATION_CODE,
            )

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Created provider configuration",
                provider=provider_id,
                workspace_id=self.workspace_id,
            )
            return integration

    def get_provider_config(
        self,
        *,
        integration: OAuthIntegration,
        default_scopes: list[str] | None = None,
    ) -> ProviderConfig | None:
        """Get decrypted client credentials for a provider."""

        if not integration or not integration.use_workspace_credentials:
            return None

        if (
            not integration.encrypted_client_id
            or not integration.encrypted_client_secret
        ):
            return None

        try:
            client_id = self.decrypt_client_credential(integration.encrypted_client_id)
            client_secret = self.decrypt_client_credential(
                integration.encrypted_client_secret
            )
            return ProviderConfig(
                client_id=client_id,
                client_secret=SecretStr(client_secret),
                provider_config=integration.provider_config,
                scopes=self.parse_scopes(integration.requested_scopes)
                or default_scopes,
            )
        except Exception as e:
            self.logger.error(
                "Failed to decrypt client credentials",
                provider=integration.provider_id,
                workspace_id=self.workspace_id,
                error=str(e),
            )
            return None

    async def remove_provider_config(self, *, provider_id: str) -> bool:
        """Remove provider configuration (client credentials) for a workspace."""
        integration = await self.get_integration(provider_id=provider_id)

        if not integration:
            return False

        # If integration has tokens, just clear client credentials
        if (
            integration.encrypted_access_token
            and integration.encrypted_access_token != b""
        ):
            integration.encrypted_client_id = None
            integration.encrypted_client_secret = None
            integration.use_workspace_credentials = False

            self.session.add(integration)
            await self.session.commit()

            self.logger.info(
                "Removed provider configuration, kept tokens",
                provider=provider_id,
                workspace_id=self.workspace_id,
            )
        else:
            # No tokens, remove entire integration record
            await self.session.delete(integration)
            await self.session.commit()

            self.logger.info(
                "Removed provider configuration completely",
                provider=provider_id,
                workspace_id=self.workspace_id,
            )

        return True

    def parse_scopes(self, scopes: str | None) -> list[str] | None:
        """Parse a space-separated string of scopes into a list of scopes."""
        return scopes.split(" ") if scopes else None
