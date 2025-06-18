"""Service for managing user integrations with external services."""

import os
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from tracecat.db.schemas import WorkspaceIntegration
from tracecat.identifiers import UserID, WorkspaceID
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
        workspace_id: WorkspaceID,
        provider: str,
        user_id: UserID | None = None,
    ) -> WorkspaceIntegration | None:
        """Get a user's integration for a specific provider."""
        statement = select(WorkspaceIntegration).where(
            WorkspaceIntegration.owner_id == workspace_id,
            WorkspaceIntegration.provider_id == provider,
        )
        if user_id is not None:
            statement = statement.where(WorkspaceIntegration.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def list_integrations(
        self, *, workspace_id: WorkspaceID
    ) -> list[WorkspaceIntegration]:
        """List all integrations for a workspace."""
        statement = select(WorkspaceIntegration).where(
            WorkspaceIntegration.owner_id == workspace_id
        )
        result = await self.session.exec(statement)
        return list(result.all())

    async def store_integration(
        self,
        *,
        workspace_id: WorkspaceID,
        provider: str,
        user_id: UserID | None = None,
        access_token: str,
        refresh_token: str | None = None,
        expires_in: int | None = None,
        scope: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceIntegration:
        """Store or update a user's integration."""
        # Calculate expiration time if expires_in is provided
        expires_at = None
        if expires_in is not None:
            expires_at = datetime.now() + timedelta(seconds=expires_in)

        # Check if integration already exists
        existing = await self.get_integration(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )

        if existing:
            # Update existing integration
            existing.encrypted_access_token = self._encrypt_token(access_token)
            existing.encrypted_refresh_token = (
                self._encrypt_token(refresh_token) if refresh_token else None
            )
            existing.expires_at = expires_at
            existing.scope = scope
            if metadata:
                # Update the metadata field
                existing.meta = metadata

            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)

            self.logger.info(
                "Updated user integration",
                user_id=user_id,
                provider=provider,
            )
            return existing
        else:
            # Create new integration
            integration = WorkspaceIntegration(
                owner_id=self.workspace_id,
                user_id=user_id,
                provider_id=provider,
                encrypted_access_token=self._encrypt_token(access_token),
                encrypted_refresh_token=self._encrypt_token(refresh_token)
                if refresh_token
                else None,
                expires_at=expires_at,
                scope=scope,
                meta=metadata or {},
            )

            self.session.add(integration)
            await self.session.commit()
            await self.session.refresh(integration)

            self.logger.info(
                "Created user integration",
                user_id=user_id,
                provider=provider,
            )
            return integration

    async def remove_integration(self, *, integration: WorkspaceIntegration) -> None:
        """Remove a user's integration for a specific provider."""
        await self.session.delete(integration)
        await self.session.commit()

    async def refresh_token_if_needed(
        self, integration: WorkspaceIntegration
    ) -> WorkspaceIntegration:
        """Refresh the access token if it's expired or about to expire."""
        if not integration.needs_refresh:
            return integration

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

        # This would need to be implemented per provider
        # For now, just log that it needs refreshing
        self.logger.warning(
            "Token refresh needed but not implemented for provider",
            user_id=integration.user_id,
            provider=integration.provider_id,
        )

        return integration

    def get_decrypted_tokens(
        self, integration: WorkspaceIntegration
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
