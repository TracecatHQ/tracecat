"""Routes for agent model access control."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.access.schemas import (
    AgentModelAccessCreate,
    AgentModelAccessListResponse,
    AgentModelAccessRead,
)
from tracecat.agent.access.service import AgentModelAccessService
from tracecat.auth.dependencies import OrgUserRole
from tracecat.db.engine import get_async_session
from tracecat.pagination import CursorPaginationParams

router = APIRouter()


@router.post(
    "/organization/agent-model-access",
    response_model=AgentModelAccessRead,
)
async def enable_model(
    access: AgentModelAccessCreate,
    role: OrgUserRole,
    session: AsyncSession = Depends(get_async_session),
) -> AgentModelAccessRead:
    """Enable a model for org or workspace."""
    service = AgentModelAccessService(session=session, role=role)
    return await service.enable_model(
        catalog_id=access.catalog_id,
        workspace_id=access.workspace_id,
    )


@router.get(
    "/organization/agent-model-access",
    response_model=AgentModelAccessListResponse,
)
async def list_enabled_models(
    role: OrgUserRole,
    session: AsyncSession = Depends(get_async_session),
    workspace_id: UUID | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> AgentModelAccessListResponse:
    """List enabled models with pagination."""
    service = AgentModelAccessService(session=session, role=role)
    try:
        params = CursorPaginationParams(cursor=cursor, limit=limit)
        items, next_cursor = await service.list_enabled_models(
            workspace_id=workspace_id,
            cursor_params=params,
        )
        return AgentModelAccessListResponse(
            items=items,
            next_cursor=next_cursor,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.delete(
    "/organization/agent-model-access/{access_id}",
    status_code=204,
)
async def disable_model(
    access_id: UUID,
    role: OrgUserRole,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Disable a model."""
    service = AgentModelAccessService(session=session, role=role)
    await service.disable_model(access_id)
