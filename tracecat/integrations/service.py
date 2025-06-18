"""Service for managing user integrations with external services."""

from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from tracecat.db.schemas import WorkspaceIntegration
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.service import BaseWorkspaceService


class IntegrationService(BaseWorkspaceService):
    """Service for managing user integrations."""

    service_name = "integrations"

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
            existing.access_token = access_token
            existing.refresh_token = refresh_token
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
                access_token=access_token,
                refresh_token=refresh_token,
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

        if not integration.refresh_token:
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
