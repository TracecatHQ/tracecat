from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import ProviderKey
from tracecat.integrations.providers import get_provider_class
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    BaseOAuthProvider,
    ClientCredentialsOAuthProvider,
)


async def get_provider_impl(
    provider_id: str = Path(...),
    grant_type: OAuthGrantType = Query(default=OAuthGrantType.AUTHORIZATION_CODE),
) -> ProviderInfo[type[BaseOAuthProvider]]:
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
    cls = get_provider_class(ProviderKey(id=provider_id, grant_type=grant_type))
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider ID: {provider_id}",
        )
    return ProviderInfo(
        impl=cls, key=ProviderKey(id=provider_id, grant_type=grant_type)
    )


async def get_ac_provider_impl(
    provider_id: str,
) -> ProviderInfo[type[AuthorizationCodeOAuthProvider]]:
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
    key = ProviderKey(id=provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE)
    cls = get_provider_class(key)
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
    return ProviderInfo(impl=cls, key=key)


async def get_cc_provider_impl(
    provider_id: str,
) -> ProviderInfo[type[ClientCredentialsOAuthProvider]]:
    """
    FastAPI dependency to get client credentials provider implementation by name.

    Args:
        provider_id: The name of the provider to retrieve

    Returns:
        A client credentials OAuth provider class

    Raises:
        HTTPException: If the provider is not supported or doesn't support client credentials flow
    """
    key = ProviderKey(id=provider_id, grant_type=OAuthGrantType.CLIENT_CREDENTIALS)
    cls = get_provider_class(key)
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
    return ProviderInfo(impl=cls, key=key)


class ProviderInfo[T: type[BaseOAuthProvider]](BaseModel):
    impl: T
    key: ProviderKey


ProviderInfoDep = Annotated[
    ProviderInfo[type[BaseOAuthProvider]], Depends(get_provider_impl)
]
CCProviderInfoDep = Annotated[
    ProviderInfo[type[ClientCredentialsOAuthProvider]], Depends(get_cc_provider_impl)
]
ACProviderInfoDep = Annotated[
    ProviderInfo[type[AuthorizationCodeOAuthProvider]], Depends(get_ac_provider_impl)
]
