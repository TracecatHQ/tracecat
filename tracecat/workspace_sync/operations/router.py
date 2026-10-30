"""Fast operation-oriented API for durable workspace Git synchronization."""

import asyncio
import uuid
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.schemas import ScopeDeniedResponse
from tracecat.authz.controls import check_scopes, require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.pagination import Page, PageParams, PaginationError
from tracecat.sync import PullResourceDiff
from tracecat.workspace_sync.operations.lifecycle_client import SyncLifecycleService
from tracecat.workspace_sync.operations.schemas import (
    SyncDiffPage,
    SyncOperationCommand,
    SyncOperationCreate,
    SyncOperationError,
    SyncOperationRead,
)
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.storage import read_diff, read_diff_page
from tracecat.workspace_sync.operations.types import (
    SyncOperationConflictError,
    SyncOperationStartError,
)

router = APIRouter(
    prefix="/workflows/sync/operations",
    tags=["workflows"],
    responses={
        403: {"model": ScopeDeniedResponse | SyncOperationError},
        404: {"model": SyncOperationError},
    },
)


async def _respond(request: Awaitable[SyncOperationRead]) -> SyncOperationRead:
    """Bound native Temporal requests and expose only safe errors to callers."""
    code = 503
    detail = "Git sync service unavailable. Retry the same request."
    try:
        async with asyncio.timeout(5):
            return await request
    except TracecatNotFoundError:
        code, detail = 404, "Sync operation not found"
    except SyncOperationConflictError as exc:
        code, detail = 409, str(exc)
    except SyncOperationStartError as exc:
        detail = str(exc)
    except TimeoutError:
        logger.warning("Temporal Git sync request timed out", error_type="TimeoutError")
    raise HTTPException(code, detail)


@router.post(
    "",
    status_code=202,
    responses={code: {"model": SyncOperationError} for code in (400, 409, 503)},
)
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
async def create_sync_operation(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, params: SyncOperationCreate
) -> SyncOperationRead:
    """Accept a preview and return before any Git or resource work begins."""
    check_scopes(
        role,
        "workspace_sync:push" if params.direction == "push" else "workspace_sync:pull",
    )
    if params.pull is not None and params.pull.dry_run:
        raise HTTPException(
            400,
            "Durable sync always starts with a preview; omit dry_run and confirm with apply.",
        )
    return await _respond(SyncLifecycleService(session, role).create(params))


@router.get("", responses={400: {"model": SyncOperationError}})
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
async def list_sync_operations(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: Annotated[
        int, Query(ge=config.TRACECAT__LIMIT_MIN, le=config.TRACECAT__LIMIT_CURSOR_MAX)
    ] = config.TRACECAT__LIMIT_DEFAULT,
    cursor: Annotated[str | None, Query(max_length=8192)] = None,
) -> Page[SyncOperationRead]:
    """Recover the initiating actor's operations after a reload or reconnect."""
    try:
        return await SyncOperationService(session, role).list(
            PageParams(limit=limit, cursor=cursor)
        )
    except PaginationError as exc:
        raise HTTPException(400, "Invalid operation cursor") from exc


@router.get("/{operation_id}", responses={503: {"model": SyncOperationError}})
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
async def get_sync_operation(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, operation_id: uuid.UUID
) -> SyncOperationRead:
    """Poll durable progress without starting or mutating the operation."""
    return await _respond(SyncLifecycleService(session, role).get(operation_id))


@router.post(
    "/{operation_id}/apply",
    status_code=202,
    responses={code: {"model": SyncOperationError} for code in (409, 503)},
)
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
async def apply_sync_operation(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    operation_id: uuid.UUID,
    params: SyncOperationCommand,
) -> SyncOperationRead:
    """Send a stable apply command to the existing lifecycle workflow."""
    return await _respond(
        SyncLifecycleService(session, role).command(operation_id, "apply", params)
    )


@router.post(
    "/{operation_id}/retry",
    status_code=202,
    responses={code: {"model": SyncOperationError} for code in (409, 503)},
)
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
async def retry_sync_operation(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    operation_id: uuid.UUID,
    params: SyncOperationCommand,
) -> SyncOperationRead:
    """Send a stable retry command to the existing lifecycle workflow."""
    return await _respond(
        SyncLifecycleService(session, role).command(operation_id, "retry", params)
    )


@router.get(
    "/{operation_id}/diffs",
    responses={code: {"model": SyncOperationError} for code in (400, 410)},
)
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
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
@require_scope("workspace_sync:push", "workspace_sync:pull", require_all=False)
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
