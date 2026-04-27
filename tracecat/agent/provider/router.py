"""Routes for custom LLM provider management."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderListResponse,
    AgentCustomProviderRead,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.service import AgentCustomProviderService
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams

router = APIRouter(prefix="/organization/agent-custom-providers")


@router.post(
    "",
    response_model=AgentCustomProviderRead,
)
@require_scope("agent:create")
async def create_custom_provider(
    provider: AgentCustomProviderCreate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentCustomProviderRead:
    """Create a new custom LLM provider."""
    service = AgentCustomProviderService(session=session, role=role)
    return await service.create_provider(provider)


@router.get(
    "",
    response_model=AgentCustomProviderListResponse,
)
@require_scope("agent:read")
async def list_custom_providers(
    role: OrgUserRole,
    session: AsyncDBSession,
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> AgentCustomProviderListResponse:
    """List organization custom providers with pagination."""
    service = AgentCustomProviderService(session=session, role=role)
    params = CursorPaginationParams(cursor=cursor, limit=limit)
    try:
        result = await service.list_providers(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return AgentCustomProviderListResponse(
        items=result.items,
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{provider_id}",
    response_model=AgentCustomProviderRead,
)
@require_scope("agent:read")
async def get_custom_provider(
    provider_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentCustomProviderRead:
    """Get a specific custom provider."""
    service = AgentCustomProviderService(session=session, role=role)
    try:
        return await service.get_provider(provider_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.patch(
    "/{provider_id}",
    response_model=AgentCustomProviderRead,
)
@require_scope("agent:update")
async def update_custom_provider(
    provider_id: UUID,
    updates: AgentCustomProviderUpdate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentCustomProviderRead:
    """Update custom provider configuration."""
    service = AgentCustomProviderService(session=session, role=role)
    try:
        return await service.update_provider(provider_id, updates)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.delete(
    "/{provider_id}",
    status_code=204,
)
@require_scope("agent:delete")
async def delete_custom_provider(
    provider_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    """Delete a custom provider."""
    service = AgentCustomProviderService(session=session, role=role)
    try:
        await service.delete_provider(provider_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.post(
    "/{provider_id}/refresh",
    status_code=202,
)
@require_scope("agent:update")
async def refresh_custom_provider_catalog(
    provider_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    """Trigger model discovery for a custom provider."""
    service = AgentCustomProviderService(session=session, role=role)
    try:
        await service.refresh_provider_catalog(provider_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post(
    "/validate",
    status_code=200,
)
@require_scope("agent:create")
async def validate_custom_provider_connection(
    provider: AgentCustomProviderCreate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> dict[str, bool]:
    """Test provider connectivity without saving."""
    service = AgentCustomProviderService(session=session, role=role)
    is_valid = await service.validate_provider(
        base_url=provider.base_url or "",
        api_key=provider.api_key,
        api_key_header=provider.api_key_header,
        custom_headers=provider.custom_headers,
    )
    return {"valid": is_valid}
