"""Fast operation-oriented API for durable workspace Git synchronization."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import Page, PageParams
from tracecat.sync import PullResourceDiff
from tracecat.workspace_sync.operations.schemas import (
    SyncDiffPage,
    SyncOperationCreate,
    SyncOperationError,
    SyncOperationRead,
)
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.storage import read_diff, read_diff_page
from tracecat.workspace_sync.operations.types import SyncOperationConflictError

router = APIRouter(
    prefix="/workflows/sync/operations",
    tags=["workflows"],
    responses={404: {"model": SyncOperationError}},
)


@router.post("", status_code=202, responses={409: {"model": SyncOperationError}})
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def create_sync_operation(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, params: SyncOperationCreate
) -> SyncOperationRead:
    """Accept a preview and return before any Git or resource work begins."""
    service = SyncOperationService(session, role)
    try:
        operation = await service.create(params)
    except SyncOperationConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return service.read(operation)


@router.get("")
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def list_sync_operations(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> Page[SyncOperationRead]:
    """Recover the initiating actor's operations after a reload or reconnect."""
    return await SyncOperationService(session, role).list(
        PageParams(limit=limit, cursor=cursor)
    )


@router.get("/{operation_id}")
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def get_sync_operation(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, operation_id: uuid.UUID
) -> SyncOperationRead:
    """Poll durable progress and repair a lost Temporal dispatch response."""
    service = SyncOperationService(session, role)
    try:
        operation = await service.get(operation_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, "Sync operation not found") from exc
    return service.read(operation)


@router.post(
    "/{operation_id}/apply",
    status_code=202,
    responses={409: {"model": SyncOperationError}},
)
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def apply_sync_operation(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, operation_id: uuid.UUID
) -> SyncOperationRead:
    """Confirm exactly the prepared snapshot; duplicate confirmations are harmless."""
    service = SyncOperationService(session, role)
    try:
        operation = await service.apply(operation_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, "Sync operation not found") from exc
    except SyncOperationConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return service.read(operation)


@router.post(
    "/{operation_id}/retry",
    status_code=202,
    responses={409: {"model": SyncOperationError}},
)
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def retry_sync_operation(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, operation_id: uuid.UUID
) -> SyncOperationRead:
    """Retry the failed phase with the same immutable inputs and completion receipt."""
    service = SyncOperationService(session, role)
    try:
        operation = await service.retry(operation_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, "Sync operation not found") from exc
    except SyncOperationConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return service.read(operation)


@router.get(
    "/{operation_id}/diffs",
    responses={code: {"model": SyncOperationError} for code in (400, 410)},
)
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def list_sync_diffs(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    operation_id: uuid.UUID,
    cursor: str | None = None,
) -> SyncDiffPage:
    """Read one bounded page of diff metadata, without loading file contents."""
    try:
        operation = await SyncOperationService(session, role).get(operation_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, "Sync operation not found") from exc
    if operation.artifact_key is None:
        return SyncDiffPage(items=[])
    try:
        count = (operation.summary or {}).get("diff_count", 0)
        return await read_diff_page(
            operation.artifact_key,
            cursor,
            diff_count=count if isinstance(count, int) else 0,
        )
    except ValueError as exc:
        raise HTTPException(400, "Invalid preview cursor") from exc
    except FileNotFoundError:
        raise HTTPException(
            410, "Preview artifact is no longer available. Start a fresh preview."
        ) from None


@router.get(
    "/{operation_id}/diffs/{index}",
    responses={code: {"model": SyncOperationError} for code in (404, 410)},
)
@require_scope("workspace_sync:sync", "workflow:sync", require_all=False)
async def get_sync_diff(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    operation_id: uuid.UUID,
    index: Annotated[int, Path(ge=0)],
) -> PullResourceDiff:
    """Load one selected file diff on demand."""
    try:
        operation = await SyncOperationService(session, role).get(operation_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, "Sync operation not found") from exc
    if operation.artifact_key is None or not operation.summary:
        raise HTTPException(404, "Diff not found")
    count = operation.summary.get("diff_count")
    if not isinstance(count, int) or index >= count:
        raise HTTPException(404, "Diff not found")
    try:
        return await read_diff(operation.artifact_key, index)
    except FileNotFoundError:
        raise HTTPException(
            410, "Preview artifact is no longer available. Start a fresh preview."
        ) from None
