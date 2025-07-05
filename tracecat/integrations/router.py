import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import select

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import OAuthStateDB
from tracecat.integrations.base import (
    AuthorizationCodeOAuthProvider,
)
from tracecat.integrations.dependencies import (
    ACProviderInfoDep,
    CCProviderInfoDep,
    ProviderInfoDep,
)
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType
from tracecat.integrations.models import (
    IntegrationOAuthCallback,
    IntegrationOAuthConnect,
    IntegrationRead,
    IntegrationReadMinimal,
    IntegrationTestConnectionResponse,
    IntegrationUpdate,
    ProviderKey,
    ProviderRead,
    ProviderReadMinimal,
    ProviderSchema,
)
from tracecat.integrations.providers import ProviderRegistry
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.types.auth import Role

integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])
"""Routes for managing dynamic integration states."""

providers_router = APIRouter(prefix="/providers", tags=["providers"])
"""Routes for managing static provider metadata."""


@integrations_router.get("/callback")
async def oauth_callback(
    *,
    session: AsyncDBSession,
    role: Annotated[
        Role,
        RoleACL(allow_user=True, allow_service=False, require_workspace="no"),
    ],
    code: str = Query(..., description="Authorization code from OAuth provider"),
    state: str = Query(..., description="State parameter from authorization request"),
) -> IntegrationOAuthCallback:
    """Handle OAuth callback for the specified provider.

    Validates the state parameter against the database to ensure it was issued
    by our server and hasn't expired. This prevents CSRF attacks.
    """
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID is required",
        )

    # Look up state in database with FOR UPDATE lock
    # Use FOR UPDATE lock to prevent concurrent access to the same OAuth state
    # This ensures atomic read-modify-delete operations and prevents race conditions
    # where multiple requests could process the same state simultaneously
    oauth_state_db = await session.get(OAuthStateDB, state, with_for_update=True)
    if oauth_state_db is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    # Validate state hasn't expired
    if datetime.now(UTC) >= oauth_state_db.expires_at:
        # Delete expired state
        await session.delete(oauth_state_db)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="State parameter has expired",
        )

    # Validate user matches and overwrite role with workspace context from state
    if oauth_state_db.user_id != role.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )

    # Overwrite role with workspace context from validated state
    # This is always authorization code
    key = ProviderKey(
        id=oauth_state_db.provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE
    )
    provider_impl = ProviderRegistry.get().get_class(key)
    if provider_impl is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider not found",
        )
    if not issubclass(provider_impl, AuthorizationCodeOAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth 2.0 Authorization code grant is not supported for this provider",
        )

    role = role.model_copy(update={"workspace_id": oauth_state_db.workspace_id})
    ctx_role.set(role)

    # Delete the state now that it's been used
    await session.delete(oauth_state_db)
    await session.commit()

    # Exchange code for tokens
    svc = IntegrationService(session, role=role)
    # The grant type is AC
    key = ProviderKey(id=provider_impl.id, grant_type=provider_impl.grant_type)
    integration = await svc.get_integration(provider_key=key)
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
    token_result = await provider.exchange_code_for_token(code, str(state))

    # Store integration tokens for this user
    await svc.store_integration(
        user_id=role.user_id,
        provider_key=key,
        access_token=token_result.access_token,
        refresh_token=token_result.refresh_token,
        expires_in=token_result.expires_in,
        scope=token_result.scope,
    )
    logger.info("Returning OAuth callback", status="connected", provider=key.id)

    redirect_url = f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{role.workspace_id}/integrations/{key.id}?tab=overview"
    return IntegrationOAuthCallback(
        status="connected",
        provider_id=key.id,
        redirect_url=redirect_url,
    )


# Collection-level endpoints
@integrations_router.get("")
async def list_integrations(
    role: WorkspaceUserRole, session: AsyncDBSession
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
    provider_info: ProviderInfoDep,
) -> IntegrationRead:
    """Get integration for the specified provider."""
    # Verify provider exists (this will raise 400 if not)

    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider_info.key} integration not found",
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
        else provider_info.impl.scopes.default,
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
    provider_info: ACProviderInfoDep,
) -> IntegrationOAuthConnect:
    """Initiate OAuth integration for the specified provider.

    Creates a secure state parameter stored in the database to prevent CSRF attacks.
    The state is validated on the callback to ensure it was issued by our server.
    """

    if role.workspace_id is None or role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace and user ID is required",
        )
    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )

    # This requires that the provider is configured
    provider_config = svc.get_provider_config(
        integration=integration, default_scopes=provider_info.impl.scopes.default
    )
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )
    provider = provider_info.impl.from_config(provider_config)

    # Clean up expired state entries before creating a new one
    stmt = select(OAuthStateDB).where(OAuthStateDB.expires_at < datetime.now(UTC))
    expired_states = await session.exec(stmt)
    for expired_state in expired_states:
        await session.delete(expired_state)
    await session.commit()

    # Create secure state in database
    state_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    oauth_state = OAuthStateDB(
        state=state_id,
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        provider_id=provider_info.key.id,
        expires_at=expires_at,
    )
    session.add(oauth_state)
    await session.commit()

    state = str(state_id)
    auth_url = await provider.get_authorization_url(state)

    return IntegrationOAuthConnect(auth_url=auth_url, provider_id=provider.id)


@integrations_router.post(
    "/{provider_id}/disconnect", status_code=status.HTTP_204_NO_CONTENT
)
async def disconnect_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_info: ProviderInfoDep,
) -> None:
    """Disconnect integration for the specified provider (revokes tokens but keeps configuration)."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider_info.key} integration not found",
        )
    await svc.disconnect_integration(integration=integration)


@integrations_router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_info: ProviderInfoDep,
) -> None:
    """Delete integration for the specified provider (removes the integration record completely)."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider_info.key} integration not found",
        )
    await svc.remove_integration(integration=integration)


@integrations_router.post("/{provider_id}/test")
async def test_connection(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_info: CCProviderInfoDep,
) -> IntegrationTestConnectionResponse:
    """Test client credentials connection for the specified provider."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider_info.key} integration not found",
        )

    # Check if provider is configured
    impl = provider_info.impl
    provider_config = svc.get_provider_config(
        integration=integration, default_scopes=impl.scopes.default
    )
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )

    try:
        # Create provider instance and attempt to get token
        provider = impl.from_config(provider_config)
        token_response = await provider.get_client_credentials_token()

        # Store the token if successful
        await svc.store_integration(
            provider_key=provider_info.key,
            access_token=token_response.access_token,
            expires_in=token_response.expires_in,
            scope=token_response.scope,
            provider_config=integration.provider_config,
        )

        logger.info(
            "Client credentials test successful",
            provider=provider_info.key,
            workspace_id=role.workspace_id,
        )

        return IntegrationTestConnectionResponse(
            success=True,
            provider_id=impl.id,
            message="Successfully connected using client credentials",
        )

    except Exception as e:
        logger.error(
            "Client credentials test failed",
            provider_key=provider_info.key,
            workspace_id=role.workspace_id,
            error=str(e),
        )
        return IntegrationTestConnectionResponse(
            success=False,
            provider_id=impl.id,
            message="Failed to connect using client credentials",
            error=str(e),
        )


@integrations_router.put("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_integration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: IntegrationUpdate,
    provider_info: ProviderInfoDep,
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
        provider_key=provider_info.key,
        client_id=params.client_id,
        client_secret=params.client_secret,
        provider_config=params.provider_config,
        requested_scopes=params.scopes,
    )

    logger.info(
        "Provider configuration updated",
        provider_key=provider_info.key,
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
            grant_type=provider_impl.grant_type,
        )
        items.append(item)

    return items


@providers_router.get("/{provider_id}")
async def get_provider(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_info: ProviderInfoDep,
) -> ProviderRead:
    """Get provider metadata, scopes, and schema."""
    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)

    return ProviderRead(
        grant_type=provider_info.key.grant_type,
        metadata=provider_info.impl.metadata,
        scopes=provider_info.impl.scopes,
        schema=ProviderSchema(json_schema=provider_info.impl.schema() or {}),
        integration_status=integration.status
        if integration
        else IntegrationStatus.NOT_CONFIGURED,
        redirect_uri=provider_info.impl.redirect_uri()
        if issubclass(provider_info.impl, AuthorizationCodeOAuthProvider)
        else None,
    )
