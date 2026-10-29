"""Workspace endpoints for managed resources: badges and detach."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import ColumnElement, Select, select

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole, WorkspaceUserRouteRole
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import CaseFields, ManagedDetachedCopy, ManagedResource
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.detach import DetachedCopy, ManagedDetachService
from tracecat.managed_resources.schemas import (
    DetachedCopyRead,
    ManagedResourceDetach,
    ManagedResourceRead,
)
from tracecat.pagination import Page, PageParams, PaginationError, paginate
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
        ManagedResource.workspace_id == role.workspace_id,
        # Registry repository ownership rows are org-level bookkeeping, not
        # workspace resources the UI badges.
        ManagedResource.resource_type.in_(
            [resource_type.value for resource_type in SyncResourceType]
        ),
    )
    if resource_type is not None:
        stmt = stmt.where(ManagedResource.resource_type == resource_type.value)
    page = await _paginate(
        session,
        stmt,
        PageParams(limit=limit, cursor=cursor),
        (ManagedResource.resource_type.asc(), ManagedResource.local_id.asc()),
    )
    names = await _case_field_names(session, role)
    return Page(
        items=[
            ManagedResourceRead.model_validate(row, from_attributes=True).model_copy(
                update={
                    "case_field_name": names.get(row.local_id),
                    "workflow_id": _workflow_id(row.resource_type, row.local_id),
                }
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
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
    page = await _paginate(
        session,
        select(ManagedDetachedCopy).where(
            ManagedDetachedCopy.workspace_id == role.workspace_id
        ),
        PageParams(limit=limit, cursor=cursor),
        (ManagedDetachedCopy.created_at.desc(), ManagedDetachedCopy.id.asc()),
    )
    names = await _case_field_names(session, role)
    return Page(
        items=[
            DetachedCopyRead(
                resource_type=SyncResourceType(row.resource_type),
                local_id=row.local_id,
                original_local_id=row.original_local_id,
                original_name=row.original_name,
                detached_at=row.created_at,
                case_field_name=names.get(row.local_id),
                workflow_id=_workflow_id(row.resource_type, row.local_id),
            )
            for row in page.items
        ],
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
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


async def _paginate[T](
    session: AsyncDBSession,
    stmt: Select[tuple[T]],
    page: PageParams,
    order_by: tuple[ColumnElement[Any], ...],
) -> Page[T]:
    """Paginate, answering a malformed or stale cursor with a 400."""
    try:
        return await paginate(session, stmt, page=page, order_by=order_by)
    except PaginationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=e.detail or str(e)
        ) from e


def _workflow_id(resource_type: str, local_id: uuid.UUID) -> str | None:
    """The short ``wf_`` id workflow APIs use, for a workflow row."""
    if resource_type != SyncResourceType.WORKFLOW.value:
        return None
    return WorkflowUUID.new(local_id).short()


async def _case_field_names(
    session: AsyncDBSession, role: Role
) -> dict[uuid.UUID, str]:
    """Map each case field's derived local id to its current schema key.

    Empty for callers who cannot read case fields, so the names are not
    disclosed to them.
    """
    if role.scopes is not None and not has_scope(role.scopes, "case:read"):
        return {}
    fields = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == role.workspace_id)
    )
    if fields is None:
        return {}
    # Same derivation as the case field sync adapter's local ids.
    return {uuid.uuid5(fields.id, name): name for name in fields.schema or {}}
