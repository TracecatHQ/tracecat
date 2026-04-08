"""Routes for agent model catalog."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.catalog.schemas import AgentCatalogListResponse, AgentCatalogRead
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.auth.dependencies import OrgUserRole, WorkspaceUserPathRole
from tracecat.db.engine import get_async_session
from tracecat.pagination import CursorPaginationParams

router = APIRouter()


@router.get(
    "/organization/agent-catalog",
    response_model=AgentCatalogListResponse,
)
async def list_catalog(
    role: OrgUserRole,
    session: AsyncSession = Depends(get_async_session),
    provider: str | None = Query(None),
    model_name: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> AgentCatalogListResponse:
    """List catalog entries with optional filtering and pagination."""
    service = AgentCatalogService(session=session)
    try:
        params = CursorPaginationParams(cursor=cursor, limit=limit)
        items, next_cursor = await service.list_catalog(
            org_id=role.organization_id,
            provider_filter=provider,
            model_name_filter=model_name,
            cursor_params=params,
        )
        return AgentCatalogListResponse(items=items, next_cursor=next_cursor)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get(
    "/organization/agent-catalog/{catalog_id}",
    response_model=AgentCatalogRead,
)
async def get_catalog_entry(
    catalog_id: UUID,
    role: OrgUserRole,
    session: AsyncSession = Depends(get_async_session),
) -> AgentCatalogRead:
    """Get a specific catalog entry."""
    service = AgentCatalogService(session=session)
    return await service.get_catalog_entry(
        org_id=role.organization_id,
        catalog_id=catalog_id,
    )


@router.get(
    "/workspaces/{workspace_id}/agent-models",
    response_model=AgentCatalogListResponse,
)
async def get_workspace_models(
    workspace_id: UUID,
    role: WorkspaceUserPathRole,
    session: AsyncSession = Depends(get_async_session),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> AgentCatalogListResponse:
    """Get models accessible to a workspace."""
    if role.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this workspace",
        )

    access_service = AgentModelAccessService(session=session, role=role)
    try:
        models = await access_service.get_workspace_models(workspace_id=workspace_id)
        truncated = models[:limit] if len(models) > limit else models
        return AgentCatalogListResponse(items=truncated, next_cursor=None)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
