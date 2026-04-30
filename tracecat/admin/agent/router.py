"""Platform-level agent administration endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.catalog.schemas import AgentCatalogListResponse
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.pagination import CursorPaginationParams

router = APIRouter(prefix="/agent", tags=["admin:agent"])


@router.get("/catalog", response_model=AgentCatalogListResponse)
async def list_platform_catalog(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    provider: str | None = Query(None),
    model_name: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> AgentCatalogListResponse:
    """List platform-owned agent catalog entries."""
    service = AgentCatalogService(session=session)
    try:
        params = CursorPaginationParams(cursor=cursor, limit=limit)
        items, next_cursor = await service.list_platform_catalog(
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
