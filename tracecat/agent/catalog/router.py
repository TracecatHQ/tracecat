from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.schemas import BuiltInCatalogRead, BuiltInProviderRead
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession

router = APIRouter(tags=["agent"])


@router.get("/catalog/platform")
@require_scope("agent:read")
async def list_platform_catalog(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    query: str | None = Query(default=None, description="Search models by name."),
    provider: str | None = Query(default=None, description="Filter by provider."),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor for the next built-in catalog page.",
    ),
    limit: int = Query(default=100, ge=1, le=200),
) -> BuiltInCatalogRead:
    service = AgentCatalogService(session, role=role)
    try:
        return await service.list_builtin_catalog(
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


@router.get("/providers")
@require_scope("agent:read")
async def list_providers(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    configured_only: bool = Query(
        default=True,
        description="Return only providers with configured credentials.",
    ),
    include_discovered_models: bool = Query(
        default=False,
        description="Include discovered built-in catalog models for each provider.",
    ),
) -> list[BuiltInProviderRead]:
    service = AgentCatalogService(session, role=role)
    return await service.list_providers(
        configured_only=configured_only,
        include_discovered_models=include_discovered_models,
    )
