"""Routes for agent model catalog."""

from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.catalog.schemas import (
    AgentCatalogCreate,
    AgentCatalogListResponse,
    AgentCatalogRead,
    AgentCatalogUpdate,
)
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.auth.dependencies import OrgUserRole, WorkspaceUserPathRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginationParams

router = APIRouter()


@router.get(
    "/organization/agent-catalog",
    response_model=AgentCatalogListResponse,
)
@require_scope("agent:read")
async def list_catalog(
    role: OrgUserRole,
    session: AsyncDBSession,
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
@require_scope("agent:read")
async def get_catalog_entry(
    catalog_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentCatalogRead:
    """Get a specific catalog entry."""
    assert role.organization_id is not None  # OrgUserRole guarantees this
    service = AgentCatalogService(session=session)
    try:
        row = await service.get_catalog_entry(
            org_id=role.organization_id,
            catalog_id=catalog_id,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return AgentCatalogRead.model_validate(row)


@router.post(
    "/organization/agent-catalog",
    response_model=AgentCatalogRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("agent:create")
async def create_catalog_entry(
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AgentCatalogCreate = Body(...),
) -> AgentCatalogRead:
    """Create an org-scoped catalog entry.

    Does not auto-enable the model; enablement is handled separately via the
    model-access API or a one-time migration.
    """
    assert role.organization_id is not None  # OrgUserRole guarantees this
    service = AgentCatalogService(session=session)
    metadata = params.model_dump(exclude={"model_provider", "model_name"})
    try:
        row = await service.create_catalog_entry(
            org_id=role.organization_id,
            model_provider=params.model_provider,
            model_name=params.model_name,
            metadata=metadata,
        )
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    return AgentCatalogRead.model_validate(row)


@router.patch(
    "/organization/agent-catalog/{catalog_id}",
    response_model=AgentCatalogRead,
)
@require_scope("agent:update")
async def update_catalog_entry(
    catalog_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AgentCatalogUpdate = Body(...),
) -> AgentCatalogRead:
    """Update metadata on an org-scoped catalog entry."""
    assert role.organization_id is not None  # OrgUserRole guarantees this
    service = AgentCatalogService(session=session)
    try:
        row = await service.get_catalog_entry(
            org_id=role.organization_id,
            catalog_id=catalog_id,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    metadata = params.model_dump(exclude={"model_provider"}, exclude_unset=True)
    try:
        updated = await service.update_catalog_entry(
            row,
            org_id=role.organization_id,
            expected_provider=params.model_provider,
            metadata=metadata,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return AgentCatalogRead.model_validate(updated)


@router.delete(
    "/organization/agent-catalog/{catalog_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("agent:delete")
async def delete_catalog_entry(
    catalog_id: UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    """Delete an org-scoped catalog entry."""
    assert role.organization_id is not None  # OrgUserRole guarantees this
    service = AgentCatalogService(session=session)
    try:
        row = await service.get_catalog_entry(
            org_id=role.organization_id,
            catalog_id=catalog_id,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    try:
        await service.delete_catalog_entry(row, org_id=role.organization_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.get(
    "/workspaces/{workspace_id}/agent-models",
    response_model=AgentCatalogListResponse,
)
@require_scope("agent:read")
async def get_workspace_models(
    workspace_id: UUID,
    role: WorkspaceUserPathRole,
    session: AsyncDBSession,
) -> AgentCatalogListResponse:
    """Get models accessible to a workspace.

    Returns the full effective set; the list is bounded by org enablement
    and is expected to be small, so pagination is not used here.
    """
    if role.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this workspace",
        )

    access_service = AgentModelAccessService(session=session, role=role)
    try:
        models = await access_service.get_workspace_models(workspace_id=workspace_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return AgentCatalogListResponse(items=models, next_cursor=None)
