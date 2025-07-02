from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from tracecat.integrations.base import (
    AuthorizationCodeOAuthProvider,
    BaseOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers import ProviderRegistry


async def get_provider_impl(provider_id: str) -> type[BaseOAuthProvider]:
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


async def get_ac_provider_impl(
    provider_id: str,
) -> type[AuthorizationCodeOAuthProvider]:
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
    if not issubclass(cls, AuthorizationCodeOAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} does not support authorization code flow",
        )
    return cls


async def get_cc_provider_impl(
    provider_id: str,
) -> type[ClientCredentialsOAuthProvider]:
    """
    FastAPI dependency to get client credentials provider implementation by name.

    Args:
        provider_id: The name of the provider to retrieve

    Returns:
        A client credentials OAuth provider class

    Raises:
        HTTPException: If the provider is not supported or doesn't support client credentials flow
    """
    cls = ProviderRegistry.get().get_class(provider_id)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider ID: {provider_id}",
        )
    if not issubclass(cls, ClientCredentialsOAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} does not support client credentials flow",
        )
    return cls


ProviderImplDep = Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)]
CCProviderImplDep = Annotated[
    type[ClientCredentialsOAuthProvider], Depends(get_cc_provider_impl)
]
ACProviderImplDep = Annotated[
    type[AuthorizationCodeOAuthProvider], Depends(get_ac_provider_impl)
]
