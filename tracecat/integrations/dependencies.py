from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    BaseOAuthProvider,
    ClientCredentialsOAuthProvider,
    JWTBearerOAuthProvider,
)
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService


async def _resolve_provider_info(
    *,
    provider_id: str,
    grant_type: OAuthGrantType,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> ProviderInfo[type[BaseOAuthProvider]]:
    key = ProviderKey(id=provider_id, grant_type=grant_type)
    svc = IntegrationService(session, role=role)
    provider_impl = await svc.resolve_provider_impl(provider_key=key)
    if provider_impl is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider ID: {provider_id}",
        )
    return ProviderInfo(impl=provider_impl, key=key)


async def get_provider_impl(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
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
    return await _resolve_provider_info(
        provider_id=provider_id,
        grant_type=grant_type,
        role=role,
        session=session,
    )


async def get_ac_provider_impl(
    provider_id: str,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
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
    info = await _resolve_provider_info(
        provider_id=provider_id,
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        role=role,
        session=session,
    )
    if not issubclass(info.impl, AuthorizationCodeOAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} does not support authorization code flow",
        )
    return ProviderInfo(impl=info.impl, key=info.key)


async def get_cc_provider_impl(
    provider_id: str,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
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
    info = await _resolve_provider_info(
        provider_id=provider_id,
        grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
        role=role,
        session=session,
    )
    if not issubclass(info.impl, ClientCredentialsOAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} does not support client credentials flow",
        )
    return ProviderInfo(impl=info.impl, key=info.key)


async def get_jwt_bearer_provider_impl(
    provider_id: str,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> ProviderInfo[type[JWTBearerOAuthProvider]]:
    """
    FastAPI dependency to get JWT bearer provider implementation by name.

    Args:
        provider_id: The name of the provider to retrieve

    Returns:
        A JWT bearer OAuth provider class

    Raises:
        HTTPException: If the provider is not supported or doesn't support JWT bearer flow
    """
    info = await _resolve_provider_info(
        provider_id=provider_id,
        grant_type=OAuthGrantType.JWT_BEARER,
        role=role,
        session=session,
    )
    if not issubclass(info.impl, JWTBearerOAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {provider_id} does not support JWT bearer flow",
        )
    return ProviderInfo(impl=info.impl, key=info.key)


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
JWTBearerProviderInfoDep = Annotated[
    ProviderInfo[type[JWTBearerOAuthProvider]], Depends(get_jwt_bearer_provider_impl)
]
