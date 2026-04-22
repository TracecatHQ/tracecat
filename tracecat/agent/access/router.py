"""Routes for agent model access control."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.access.schemas import (
    AgentModelAccessCreate,
    AgentModelAccessListResponse,
    AgentModelAccessRead,
)
from tracecat.agent.access.service import AgentModelAccessService
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginationParams

router = APIRouter(prefix="/organization/agent-model-access")


@router.post(
    "",
    response_model=AgentModelAccessRead,
)
@require_scope("agent:create")
async def enable_model(
    access: AgentModelAccessCreate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentModelAccessRead:
    """Enable a model for org or workspace."""
    service = AgentModelAccessService(session=session, role=role)
    try:
        return await service.enable_model(
            catalog_id=access.catalog_id,
            workspace_id=access.workspace_id,
        )
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.get(
    "",
    response_model=AgentModelAccessListResponse,
)
@require_scope("agent:read")
async def list_enabled_models(
    role: OrgUserRole,
    session: AsyncDBSession,
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
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.delete(
    "/{access_id}",
    status_code=204,
)
@require_scope("agent:delete")
async def disable_model(
    access_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    """Disable a model."""
    service = AgentModelAccessService(session=session, role=role)
    try:
        await service.disable_model(access_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
