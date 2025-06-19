from fastapi import HTTPException, status

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.providers import ProviderRegistry
from tracecat.integrations.service import IntegrationService


async def get_provider(
    provider_id: str,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> BaseOauthProvider:
    """
    FastAPI dependency to get provider implementation by name.

    Args:
        provider_id: The name of the provider to retrieve
        role: The workspace user role (dependency)
        session: The database session (dependency)

    Returns:
        An instance of the requested OAuth provider with workspace credentials if available

    Raises:
        HTTPException: If the provider is not supported
    """
    cls = ProviderRegistry.get().get_class(provider_id)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider ID: {provider_id}",
        )

    # Try to get workspace-configured client credentials
    if role.workspace_id:
        integration_service = IntegrationService(session, role=role)
        workspace_credentials = await integration_service.get_provider_config(
            provider=provider_id
        )

        if workspace_credentials:
            client_id, client_secret = workspace_credentials
            return cls(client_id=client_id, client_secret=client_secret)

    # Fallback to environment variables
    return cls()
