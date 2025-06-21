from fastapi import HTTPException, status

from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.providers import ProviderRegistry


async def get_provider(provider_id: str) -> type[BaseOAuthProvider]:
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
    return cls
