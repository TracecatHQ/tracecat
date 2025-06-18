from fastapi import HTTPException, status

from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.providers import get_provider as get_provider_class


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
    cls = get_provider_class(provider_id)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider ID: {provider_id}",
        )
    return cls()
