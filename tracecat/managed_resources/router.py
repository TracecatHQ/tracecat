"""Workspace endpoints for managed resources: badges and detach."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole, WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import ManagedDetachedCopy, ManagedResource
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.managed_resources.detach import DetachedCopy, ManagedDetachService
from tracecat.managed_resources.schemas import (
    DetachedCopyRead,
    ManagedResourceDetach,
    ManagedResourceRead,
)
from tracecat.pagination import Page, PageParams, paginate
from tracecat.workspace_sync.enums import SyncResourceType

router = APIRouter(prefix="/managed-resources", tags=["managed-resources"])

_Limit = Annotated[
    int, Query(ge=config.TRACECAT__LIMIT_MIN, le=config.TRACECAT__LIMIT_CURSOR_MAX)
]
_Cursor = Annotated[str | None, Query(max_length=8192)]


@router.get("", response_model=Page[ManagedResourceRead])
@require_scope("workspace:read")
async def list_managed_resources(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    resource_type: SyncResourceType | None = None,
    limit: _Limit = config.TRACECAT__LIMIT_DEFAULT,
    cursor: _Cursor = None,
) -> Page[ManagedResourceRead]:
    """List the workspace's managed resources, optionally of one type."""
    stmt = select(ManagedResource).where(
        ManagedResource.workspace_id == role.workspace_id
    )
    if resource_type is not None:
        stmt = stmt.where(ManagedResource.resource_type == resource_type.value)
    page = await paginate(
        session,
        stmt,
        page=PageParams(limit=limit, cursor=cursor),
        order_by=(ManagedResource.resource_type.asc(), ManagedResource.local_id.asc()),
    )
    return Page(
        items=[
            ManagedResourceRead.model_validate(row, from_attributes=True)
            for row in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/detached", response_model=Page[DetachedCopyRead])
@require_scope("workspace:read")
async def list_detached_copies(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: _Limit = config.TRACECAT__LIMIT_DEFAULT,
    cursor: _Cursor = None,
) -> Page[DetachedCopyRead]:
    """List copies made by detaching managed resources, for "detached from X"."""
    page = await paginate(
        session,
        select(ManagedDetachedCopy).where(
            ManagedDetachedCopy.workspace_id == role.workspace_id
        ),
        page=PageParams(limit=limit, cursor=cursor),
        order_by=(ManagedDetachedCopy.created_at.desc(), ManagedDetachedCopy.id.asc()),
    )
    return Page(
        items=[
            DetachedCopyRead(
                resource_type=SyncResourceType(row.resource_type),
                local_id=row.local_id,
                original_local_id=row.original_local_id,
                original_name=row.original_name,
                detached_at=row.created_at,
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.post("/detach", response_model=DetachedCopy, status_code=201)
@require_scope("workspace:update")
async def detach_managed_resource(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    params: ManagedResourceDetach,
) -> DetachedCopy:
    """Copy a managed resource into an editable, customer-owned resource."""
    service = ManagedDetachService(session, role=role)
    try:
        return await service.detach(
            resource_type=params.resource_type,
            local_id=params.local_id,
            new_name=params.new_name,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
