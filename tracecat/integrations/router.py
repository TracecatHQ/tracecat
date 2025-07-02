import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from tracecat import config
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.dependencies import get_provider_impl
from tracecat.integrations.enums import IntegrationStatus
from tracecat.integrations.models import (
    IntegrationOAuthCallback,
    IntegrationOAuthConnect,
    IntegrationRead,
    IntegrationReadMinimal,
    IntegrationUpdate,
    OAuthState,
    ProviderRead,
    ProviderReadMinimal,
    ProviderSchema,
)
from tracecat.integrations.providers import ProviderRegistry
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger

integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])
"""Routes for managing dynamic integration states."""

providers_router = APIRouter(prefix="/providers", tags=["providers"])
"""Routes for managing static provider metadata."""


# Collection-level endpoints
@integrations_router.get("")
async def list_integrations(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[IntegrationReadMinimal]:
    """List all integrations for the current user."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    integration_service = IntegrationService(session, role=role)
    integrations = await integration_service.list_integrations()

    # Convert to response models (excluding sensitive data like tokens)
    return [
        IntegrationReadMinimal(
            id=integration.id,
            provider_id=integration.provider_id,
            status=integration.status,
            is_expired=integration.is_expired,
        )
        for integration in integrations
    ]


@integrations_router.get("/{provider_id}")
async def get_integration(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_impl: Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)],
) -> IntegrationRead:
    """Get integration for the specified provider."""
    # Verify provider exists (this will raise 400 if not)

    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_id=provider_impl.id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider_impl.id} integration not found",
        )

    return IntegrationRead(
        id=integration.id,
        user_id=integration.user_id,
        provider_id=integration.provider_id,
        token_type=integration.token_type,
        expires_at=integration.expires_at,
        granted_scopes=integration.scope.split(" ") if integration.scope else None,
        requested_scopes=integration.requested_scopes.split(" ")
        if integration.requested_scopes
        else provider_impl.scopes.default,
        provider_config=integration.provider_config,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        status=integration.status,
        is_expired=integration.is_expired,
        client_id=(
            svc.decrypt_client_credential(integration.encrypted_client_id)
            if integration.encrypted_client_id
            else None
        ),
    )


# XXX(SECURITY): Should we allow non-admins to connect providers?
# Generic OAuth flow endpoints
@integrations_router.post("/{provider_id}/connect")
async def connect_provider(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_impl: Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)],
) -> IntegrationOAuthConnect:
    """Initiate OAuth integration for the specified provider."""

    if role.workspace_id is None or role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace and user ID is required",
        )
    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_id=provider_impl.id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )

    # This requires that the provider is configured
    provider_config = svc.get_provider_config(
        integration=integration, default_scopes=provider_impl.scopes.default
    )
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )
    provider = provider_impl.from_config(provider_config)
    state = f"{role.workspace_id}:{role.user_id}:{uuid.uuid4()}"
    auth_url = await provider.get_authorization_url(state)

    return IntegrationOAuthConnect(auth_url=auth_url, provider_id=provider.id)


@integrations_router.get("/{provider_id}/callback")
async def oauth_callback(
    *,
    session: AsyncDBSession,
    role: WorkspaceUserRole,
    code: str = Query(..., description="Authorization code from OAuth provider"),
    state: str = Query(..., description="State parameter from authorization request"),
    provider_impl: Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)],
) -> IntegrationOAuthCallback:
    """Handle OAuth callback for the specified provider."""
    if role.workspace_id is None or role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace and user ID is required",
        )

    logger.info("OAuth callback", provider_id=provider_impl.id)
    # Verify state contains user ID
    try:
        oauth_state = OAuthState.from_state(state)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state format",
        ) from e

    if (
        oauth_state.user_id != role.user_id
        or oauth_state.workspace_id != role.workspace_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )

    # Exchange code for tokens
    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_id=provider_impl.id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )

    # This requires that the provider is configured
    provider_config = svc.get_provider_config(
        integration=integration, default_scopes=provider_impl.scopes.default
    )
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )
    provider = provider_impl.from_config(provider_config)

    token_result = await provider.exchange_code_for_token(code, state)

    logger.info("OAuth callback")

    # Store integration tokens for this user
    await svc.store_integration(
        user_id=role.user_id,
        provider_id=provider.id,
        access_token=token_result.access_token,
        refresh_token=token_result.refresh_token,
        expires_in=token_result.expires_in,
        scope=token_result.scope,
    )
    logger.info("Returning OAuth callback", status="connected", provider=provider.id)

    redirect_url = (
        f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{role.workspace_id}/integrations"
    )
    return IntegrationOAuthCallback(
        status="connected",
        provider_id=provider.id,
        redirect_url=redirect_url,
    )


@integrations_router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_impl: Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)],
) -> None:
    """Disconnect integration for the specified provider."""
    # Verify provider exists (this will raise 400 if not)
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_id=provider_impl.id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider_impl.id} integration not found",
        )
    await svc.disconnect_integration(integration=integration)


@integrations_router.put("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: IntegrationUpdate,
    provider_impl: Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)],
) -> None:
    """Update OAuth client credentials for the specified provider integration."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    # Store the provider configuration
    await svc.store_provider_config(
        provider_id=provider_impl.id,
        client_id=params.client_id,
        client_secret=params.client_secret,
        provider_config=params.provider_config,
        requested_scopes=params.scopes,
    )

    logger.info(
        "Provider configuration updated",
        provider_id=provider_impl.id,
        workspace_id=role.workspace_id,
    )


# Provider discovery endpoints


@providers_router.get("")
async def list_providers(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[ProviderReadMinimal]:
    svc = IntegrationService(session, role=role)
    existing = {i.provider_id: i for i in await svc.list_integrations()}

    items: list[ProviderReadMinimal] = []
    for provider_impl in ProviderRegistry.get().providers:
        integration = existing.get(provider_impl.id)
        metadata = provider_impl.metadata
        item = ProviderReadMinimal(
            id=provider_impl.id,
            name=metadata.name,
            description=metadata.description,
            requires_config=metadata.requires_config,
            categories=metadata.categories,
            integration_status=integration.status
            if integration
            else IntegrationStatus.NOT_CONFIGURED,
            enabled=metadata.enabled,
        )
        items.append(item)

    return items


@providers_router.get("/{provider_id}")
async def get_provider(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_impl: Annotated[type[BaseOAuthProvider], Depends(get_provider_impl)],
) -> ProviderRead:
    """Get provider metadata, scopes, and schema."""
    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_id=provider_impl.id)

    return ProviderRead(
        metadata=provider_impl.metadata,
        scopes=provider_impl.scopes,
        schema=ProviderSchema(json_schema=provider_impl.schema() or {}),
        integration_status=integration.status
        if integration
        else IntegrationStatus.NOT_CONFIGURED,
        redirect_uri=provider_impl.redirect_uri(),
    )
