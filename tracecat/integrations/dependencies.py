from fastapi import HTTPException, status

from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.providers import ProviderRegistry


def get_provider(provider_id: str) -> BaseOauthProvider:
    """
    FastAPI dependency to get provider implementation by name.

    Args:
        provider: The name of the provider to retrieve

    Returns:
        An instance of the requested OAuth provider

    Raises:
        HTTPException: If the provider is not supported
    """
    cls = ProviderRegistry.get().get_class(provider_id)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider ID: {provider_id}",
        )
    return cls()
