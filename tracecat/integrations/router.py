import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import SecretStr
from sqlalchemy import select

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OAuthStateDB
from tracecat.integrations.dependencies import (
    ACProviderInfoDep,
    CCProviderInfoDep,
    ProviderInfoDep,
)
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType
from tracecat.integrations.providers import all_providers
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
)
from tracecat.integrations.schemas import (
    CustomOAuthProviderCreate,
    IntegrationOAuthCallback,
    IntegrationOAuthConnect,
    IntegrationRead,
    IntegrationReadMinimal,
    IntegrationTestConnectionResponse,
    IntegrationUpdate,
    MCPIntegrationCreate,
    MCPIntegrationRead,
    MCPIntegrationUpdate,
    ProviderKey,
    ProviderRead,
    ProviderReadMinimal,
    ProviderSchema,
)
from tracecat.integrations.service import (
    InsecureOAuthEndpointError,
    IntegrationService,
)
from tracecat.logger import logger

integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])
"""Routes for managing dynamic integration states."""

providers_router = APIRouter(prefix="/providers", tags=["providers"])
"""Routes for managing static provider metadata."""

mcp_router = APIRouter(prefix="/mcp-integrations", tags=["mcp-integrations"])
"""Routes for managing MCP integrations."""


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
    role = role.model_copy(update={"workspace_id": oauth_state_db.workspace_id})
    ctx_role.set(role)

    # Create service to resolve provider (including custom providers)
    svc = IntegrationService(session, role=role)
    key = ProviderKey(
        id=oauth_state_db.provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE
    )
    provider_impl = await svc.resolve_provider_impl(provider_key=key)
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

    # Extract code_verifier before deleting state (needed for PKCE flows)
    code_verifier = oauth_state_db.code_verifier

    # Delete the state now that it's been used
    await session.delete(oauth_state_db)
    await session.commit()

    # Exchange code for tokens
    integration = await svc.get_integration(provider_key=key)

    provider_config = (
        svc.get_provider_config(
            integration=integration,
            provider_impl=provider_impl,
            default_scopes=provider_impl.scopes.default,
        )
        if integration
        else None
    )

    try:
        if provider_impl.metadata.requires_config:
            if integration is None or provider_config is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Provider is not configured for this workspace",
                )
            provider = await provider_impl.instantiate(config=provider_config)
        else:
            provider = await provider_impl.instantiate(config=provider_config)
            if (integration is None or provider_config is None) and provider.client_id:
                await svc.store_provider_config(
                    provider_key=key,
                    client_id=provider.client_id,
                    client_secret=SecretStr(provider.client_secret)
                    if provider.client_secret
                    else None,
                    authorization_endpoint=provider.authorization_endpoint,
                    token_endpoint=provider.token_endpoint,
                    requested_scopes=provider.requested_scopes,
                )
    except InsecureOAuthEndpointError as exc:
        logger.warning(
            "Rejected insecure OAuth endpoint during OAuth callback",
            provider=provider_impl.id,
            grant_type=provider_impl.grant_type,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (ValueError, httpx.HTTPError, RuntimeError, KeyError) as exc:
        # Log sanitized error details without exposing implementation
        error_msg = str(exc)
        if isinstance(exc, httpx.HTTPError):
            error_type = "network_error"
        elif isinstance(exc, RuntimeError):
            error_type = "runtime_error"
        elif isinstance(exc, KeyError):
            error_type = "configuration_error"
        else:
            error_type = "validation_error"

        logger.error(
            "Failed to instantiate OAuth provider",
            provider=provider_impl.id,
            grant_type=provider_impl.grant_type,
            error_type=error_type,
            # Sanitize error message to avoid exposing sensitive details
            error=error_msg[:200] if error_msg else "Unknown error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Provider configuration or credentials are not available",
        ) from exc
    token_result = await provider.exchange_code_for_token(
        code, str(state), code_verifier
    )

    # Store integration tokens for this user
    try:
        await svc.store_integration(
            user_id=role.user_id,
            provider_key=key,
            access_token=token_result.access_token,
            refresh_token=token_result.refresh_token,
            expires_in=token_result.expires_in,
            scope=token_result.scope,
            authorization_endpoint=provider.authorization_endpoint,
            token_endpoint=provider.token_endpoint,
        )
    except InsecureOAuthEndpointError as exc:
        logger.warning(
            "Rejected insecure OAuth endpoint when storing integration",
            provider=key.id,
            grant_type=key.grant_type,
            workspace_id=role.workspace_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Provider returned insecure OAuth endpoints",
        ) from exc
    logger.info("Returning OAuth callback", status="connected", provider=key.id)

    redirect_url = (
        f"{config.TRACECAT__PUBLIC_APP_URL}/workspaces/{role.workspace_id}/integrations"
    )
    return IntegrationOAuthCallback(
        status="connected",
        provider_id=key.id,
        redirect_url=redirect_url,
    )


# Collection-level endpoints
@integrations_router.get("")
@require_scope("workflow:read")
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
@require_scope("workflow:read")
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

    authorization_endpoint, token_endpoint = svc._determine_endpoints(
        provider_info.impl,
        configured_authorization=integration.authorization_endpoint,
        configured_token=integration.token_endpoint,
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
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
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
    provider_impl = provider_info.impl
    integration = await svc.get_integration(provider_key=provider_info.key)
    provider_config = (
        svc.get_provider_config(
            integration=integration,
            provider_impl=provider_impl,
            default_scopes=provider_impl.scopes.default,
        )
        if integration
        else None
    )

    try:
        if provider_impl.metadata.requires_config:
            if integration is None or provider_config is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Provider is not configured for this workspace",
                )
            provider = await provider_impl.instantiate(config=provider_config)
        else:
            provider = await provider_impl.instantiate(config=provider_config)
            if (integration is None or provider_config is None) and provider.client_id:
                await svc.store_provider_config(
                    provider_key=provider_info.key,
                    client_id=provider.client_id,
                    client_secret=SecretStr(provider.client_secret)
                    if provider.client_secret
                    else None,
                    authorization_endpoint=provider.authorization_endpoint,
                    token_endpoint=provider.token_endpoint,
                    requested_scopes=provider.requested_scopes,
                )
                # Refresh integration context with stored credentials for subsequent operations
                integration = await svc.get_integration(provider_key=provider_info.key)
                provider_config = (
                    svc.get_provider_config(
                        integration=integration,
                        provider_impl=provider_impl,
                        default_scopes=provider_impl.scopes.default,
                    )
                    if integration
                    else None
                )
    except InsecureOAuthEndpointError as exc:
        logger.warning(
            "Rejected insecure OAuth endpoint while preparing authorization",
            provider=provider_impl.id,
            grant_type=provider_impl.grant_type,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (ValueError, httpx.HTTPError, RuntimeError, KeyError) as exc:
        # Log sanitized error details without exposing implementation
        error_msg = str(exc)
        if isinstance(exc, httpx.HTTPError):
            error_type = "network_error"
        elif isinstance(exc, RuntimeError):
            error_type = "runtime_error"
        elif isinstance(exc, KeyError):
            error_type = "configuration_error"
        else:
            error_type = "validation_error"

        logger.error(
            "Failed to instantiate OAuth provider",
            provider=provider_impl.id,
            grant_type=provider_impl.grant_type,
            error_type=error_type,
            # Sanitize error message to avoid exposing sensitive details
            error=error_msg[:200] if error_msg else "Unknown error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Provider configuration or credentials are not available",
        ) from exc

    # Clean up expired state entries before creating a new one
    stmt = select(OAuthStateDB).where(OAuthStateDB.expires_at < datetime.now(UTC))
    result = await session.execute(stmt)
    expired_states = result.scalars().all()
    for expired_state in expired_states:
        await session.delete(expired_state)
    await session.commit()

    # Create secure state in database (without code_verifier initially)
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

    # Generate authorization URL and get code_verifier if PKCE is used
    state = str(state_id)
    auth_url, code_verifier = await provider.get_authorization_url(state)

    # Store code_verifier if present (for PKCE flows)
    if code_verifier:
        oauth_state.code_verifier = code_verifier
        await session.commit()

    logger.info(
        "Generated authorization URL",
        provider=provider.id,
        has_code_verifier=code_verifier is not None,
    )

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

    # For custom providers, delete the provider definition even if no integration exists
    if integration is None:
        if provider_info.key.id.startswith("custom_"):
            # Delete the custom provider definition if it exists
            await svc.delete_custom_provider(provider_key=provider_info.key)
            return
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
        integration=integration,
        provider_impl=impl,
        default_scopes=impl.scopes.default,
    )
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider is not configured for this workspace",
        )

    try:
        # Create provider instance and attempt to get token
        provider = await impl.instantiate(config=provider_config)
        token_response = await provider.get_client_credentials_token()

        # Store the token if successful
        await svc.store_integration(
            provider_key=provider_info.key,
            access_token=token_response.access_token,
            expires_in=token_response.expires_in,
            scope=token_response.scope,
            authorization_endpoint=provider.authorization_endpoint,
            token_endpoint=provider.token_endpoint,
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
    try:
        await svc.store_provider_config(
            provider_key=provider_info.key,
            client_id=params.client_id,
            client_secret=params.client_secret,
            authorization_endpoint=params.authorization_endpoint,
            token_endpoint=params.token_endpoint,
            requested_scopes=params.scopes,
        )
    except InsecureOAuthEndpointError as exc:
        logger.warning(
            "Rejected insecure OAuth endpoint on provider update",
            provider=provider_info.key,
            workspace_id=role.workspace_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    logger.info(
        "Provider configuration updated",
        provider_key=provider_info.key,
        workspace_id=role.workspace_id,
    )


# Provider discovery endpoints


@providers_router.post("", status_code=status.HTTP_201_CREATED)
async def create_custom_provider(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: CustomOAuthProviderCreate,
) -> ProviderReadMinimal:
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    try:
        provider = await svc.create_custom_provider(params=params)
    except InsecureOAuthEndpointError as exc:
        logger.warning(
            "Rejected insecure OAuth endpoint on custom provider create",
            workspace_id=role.workspace_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    integration = await svc.get_integration(
        provider_key=ProviderKey(
            id=provider.provider_id, grant_type=provider.grant_type
        )
    )

    return ProviderReadMinimal(
        id=provider.provider_id,
        name=provider.name,
        description=provider.description or "Custom OAuth provider",
        requires_config=True,
        integration_status=integration.status
        if integration
        else IntegrationStatus.NOT_CONFIGURED,
        enabled=True,
        grant_type=provider.grant_type,
    )


@providers_router.get("")
@require_scope("workflow:read")
async def list_providers(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[ProviderReadMinimal]:
    svc = IntegrationService(session, role=role)
    existing = {(i.provider_id, i.grant_type): i for i in await svc.list_integrations()}

    items: list[ProviderReadMinimal] = []
    for provider_impl in all_providers():
        integration = existing.get((provider_impl.id, provider_impl.grant_type))
        metadata = provider_impl.metadata
        item = ProviderReadMinimal(
            id=provider_impl.id,
            name=metadata.name,
            description=metadata.description,
            requires_config=metadata.requires_config,
            integration_status=integration.status
            if integration
            else IntegrationStatus.NOT_CONFIGURED,
            enabled=metadata.enabled,
            grant_type=provider_impl.grant_type,
        )
        items.append(item)

    for custom_provider in await svc.list_custom_providers():
        integration = existing.get(
            (custom_provider.provider_id, custom_provider.grant_type)
        )
        items.append(
            ProviderReadMinimal(
                id=custom_provider.provider_id,
                name=custom_provider.name,
                description=custom_provider.description or "Custom OAuth provider",
                requires_config=True,
                integration_status=integration.status
                if integration
                else IntegrationStatus.NOT_CONFIGURED,
                enabled=True,
                grant_type=custom_provider.grant_type,
            )
        )

    return items


@providers_router.get("/{provider_id}")
@require_scope("workflow:read")
async def get_provider(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    provider_info: ProviderInfoDep,
) -> ProviderRead:
    """Get provider metadata, scopes, and schema."""
    svc = IntegrationService(session, role=role)
    integration = await svc.get_integration(provider_key=provider_info.key)
    impl = provider_info.impl

    return ProviderRead(
        grant_type=provider_info.key.grant_type,
        metadata=impl.metadata,
        scopes=impl.scopes,
        config_schema=ProviderSchema(json_schema=impl.schema() or {}),
        integration_status=integration.status
        if integration
        else IntegrationStatus.NOT_CONFIGURED,
        default_authorization_endpoint=getattr(
            impl, "default_authorization_endpoint", None
        ),
        default_token_endpoint=getattr(impl, "default_token_endpoint", None),
        authorization_endpoint_help=getattr(impl, "authorization_endpoint_help", None),
        token_endpoint_help=getattr(impl, "token_endpoint_help", None),
        redirect_uri=impl.redirect_uri()
        if issubclass(impl, AuthorizationCodeOAuthProvider)
        else None,
    )


# MCP Integration endpoints


@mcp_router.post("", status_code=status.HTTP_201_CREATED)
async def create_mcp_integration(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: MCPIntegrationCreate,
) -> MCPIntegrationRead:
    """Create a new MCP integration."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    try:
        mcp_integration = await svc.create_mcp_integration(params=params)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return MCPIntegrationRead(
        id=mcp_integration.id,
        workspace_id=mcp_integration.workspace_id,
        name=mcp_integration.name,
        description=mcp_integration.description,
        slug=mcp_integration.slug,
        server_uri=mcp_integration.server_uri,
        auth_type=mcp_integration.auth_type,
        oauth_integration_id=mcp_integration.oauth_integration_id,
        created_at=mcp_integration.created_at,
        updated_at=mcp_integration.updated_at,
    )


@mcp_router.get("")
@require_scope("workflow:read")
async def list_mcp_integrations(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[MCPIntegrationRead]:
    """List all MCP integrations for the workspace."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integrations = await svc.list_mcp_integrations()

    return [
        MCPIntegrationRead(
            id=integration.id,
            workspace_id=integration.workspace_id,
            name=integration.name,
            description=integration.description,
            slug=integration.slug,
            server_uri=integration.server_uri,
            auth_type=integration.auth_type,
            oauth_integration_id=integration.oauth_integration_id,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
        )
        for integration in integrations
    ]


@mcp_router.get("/{mcp_integration_id}")
@require_scope("workflow:read")
async def get_mcp_integration(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    mcp_integration_id: uuid.UUID,
) -> MCPIntegrationRead:
    """Get an MCP integration by ID."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    integration = await svc.get_mcp_integration(mcp_integration_id=mcp_integration_id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP integration not found",
        )

    return MCPIntegrationRead(
        id=integration.id,
        workspace_id=integration.workspace_id,
        name=integration.name,
        description=integration.description,
        slug=integration.slug,
        server_uri=integration.server_uri,
        auth_type=integration.auth_type,
        oauth_integration_id=integration.oauth_integration_id,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


@mcp_router.put("/{mcp_integration_id}")
async def update_mcp_integration(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    mcp_integration_id: uuid.UUID,
    params: MCPIntegrationUpdate,
) -> MCPIntegrationRead:
    """Update an MCP integration."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    try:
        integration = await svc.update_mcp_integration(
            mcp_integration_id=mcp_integration_id, params=params
        )
        if integration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MCP integration not found",
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return MCPIntegrationRead(
        id=integration.id,
        workspace_id=integration.workspace_id,
        name=integration.name,
        description=integration.description,
        slug=integration.slug,
        server_uri=integration.server_uri,
        auth_type=integration.auth_type,
        oauth_integration_id=integration.oauth_integration_id,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


@mcp_router.delete("/{mcp_integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_integration(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    mcp_integration_id: uuid.UUID,
) -> None:
    """Delete an MCP integration."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    svc = IntegrationService(session, role=role)
    deleted = await svc.delete_mcp_integration(mcp_integration_id=mcp_integration_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP integration not found",
        )
