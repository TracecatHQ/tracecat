"""Platform-level agent catalog management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.admin.agent.schemas import PlatformCatalogRead
from tracecat.agent.catalog.service import AdminAgentCatalogService
from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSessionBypass

router = APIRouter(prefix="/agent", tags=["admin:agent"])


@router.get("/catalog/platform", response_model=PlatformCatalogRead)
async def list_platform_catalog(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    query: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> PlatformCatalogRead:
    service = AdminAgentCatalogService(session, role)
    try:
        return await service.list_platform_catalog(
            query=query,
            provider=provider,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
