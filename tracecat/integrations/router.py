import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from tracecat import config
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.integrations.base import BaseOauthProvider
from tracecat.integrations.dependencies import get_provider
from tracecat.integrations.models import (
    IntegrationOauthCallback,
    IntegrationRead,
    IntegrationUpdate,
    OauthState,
)
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger

router = APIRouter(prefix="/integrations", tags=["integrations"])


# Collection-level endpoints
@router.get("")
async def list_integrations(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[IntegrationRead]:
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
        IntegrationRead(
            id=integration.id,
            workspace_id=WorkspaceUUID.new(integration.owner_id).short(),
            user_id=integration.user_id,
            provider_id=integration.provider_id,
            token_type=integration.token_type,
            expires_at=integration.expires_at,
            scope=integration.scope,
            provider_config=integration.provider_config,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
        )
        for integration in integrations
    ]


# Generic OAuth flow endpoints
@router.post("/{provider_id}/connect")
async def connect_provider(
    role: WorkspaceUserRole,
    provider: BaseOauthProvider = Depends(get_provider),
) -> dict[str, str]:
    """Initiate OAuth integration for the specified provider."""

    if role.workspace_id is None or role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace and user ID is required",
        )

    state = f"{role.workspace_id}:{role.user_id}:{uuid.uuid4()}"
    auth_url = await provider.get_authorization_url(state)

    return {"auth_url": auth_url, "provider": provider.id}


@router.get("/{provider_id}/callback")
async def oauth_callback(
    *,
    provider_id: str,
    session: AsyncDBSession,
    role: WorkspaceUserRole,
    code: str = Query(..., description="Authorization code from OAuth provider"),
    state: str = Query(..., description="State parameter from authorization request"),
    provider: BaseOauthProvider = Depends(get_provider),
) -> IntegrationOauthCallback:
    """Handle OAuth callback for the specified provider."""
    if role.workspace_id is None or role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace and user ID is required",
        )

    logger.info("OAuth callback", code=code, state=state, provider=provider.id)
    # Verify state contains user ID
    try:
        oauth_state = OauthState.from_state(state)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid state format: {e}",
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
    token_result = await provider.exchange_code_for_token(code, state)

    logger.info("OAuth callback", token_result=token_result)

    # Store integration tokens for this user
    integration_service = IntegrationService(session, role=role)
    await integration_service.store_integration(
        user_id=role.user_id,
        provider=provider.id,
        access_token=token_result.access_token,
        refresh_token=token_result.refresh_token,
        expires_in=token_result.expires_in,
        scope=token_result.scope,
    )
    logger.info("Returning OAuth callback", status="connected", provider=provider.id)

    redirect_url = (
        f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{role.workspace_id}/integrations"
    )
    return IntegrationOauthCallback(
        status="connected",
        provider_id=provider.id,
        redirect_url=redirect_url,
    )


@router.delete("/{provider_id}")
async def disconnect_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider: BaseOauthProvider = Depends(get_provider),
) -> dict[str, str]:
    """Disconnect integration for the specified provider."""
    # Verify provider exists (this will raise 400 if not)
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(
        user_id=role.user_id,
        provider=provider.id,
    )
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider.id} integration not found",
        )
    await svc.remove_integration(integration=integration)

    return {"status": "disconnected", "provider": provider.id}


@router.patch("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_id: str,
    config: IntegrationUpdate,
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
        provider=provider_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )

    logger.info(
        "Provider configuration updated",
        provider_id=provider_id,
        workspace_id=role.workspace_id,
    )


@router.get("/{provider_id}/status")
async def get_integration_status(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider: BaseOauthProvider = Depends(get_provider),
) -> dict[str, Any]:
    """Get integration status for the specified provider."""
    # Verify provider exists (this will raise 400 if not)

    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(
        user_id=role.user_id,
        provider=provider.id,
    )

    # Check if provider is configured at workspace level
    is_configured = bool(
        integration
        and integration.use_workspace_credentials
        and integration.encrypted_client_id
        and integration.encrypted_client_secret
    )

    if not integration:
        return {
            "connected": False,
            "configured": is_configured,
            "expires_at": None,
            "provider": provider.id,
        }

    return {
        "connected": True,
        "configured": is_configured,
        "provider": provider.id,
        "expires_at": integration.expires_at,
        "is_expired": integration.is_expired,
        "needs_refresh": integration.needs_refresh,
    }
