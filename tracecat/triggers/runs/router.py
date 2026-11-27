"""Workspace routes for a trigger's recent runs and stopped events, and the
trigger behind a run or session. ``TriggerError`` becomes its HTTP response in
``trigger_error_handler``."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import WorkflowExecutionID
from tracecat.pagination import CursorPaginationParams, PaginationError
from tracecat.triggers.runs.schemas import (
    TriggerRunOrigin,
    TriggerRunsPage,
    TriggerStoppedEventsPage,
)
from tracecat.triggers.runs.service import TriggerRunsService

router = APIRouter(prefix="/triggers", tags=["triggers"])

LimitQuery = Annotated[
    int,
    Query(
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    ),
]


def _bad_request(e: ValueError) -> HTTPException:
    """A 400 for a bad cursor or argument, with a pagination error's code."""
    detail = e.detail if isinstance(e, PaginationError) and e.detail else str(e)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


# Before the ``/{trigger_id}/...`` routes, though no path of theirs matches it.
@router.get("/runs/origin")
@require_scope("trigger:read")
async def get_trigger_run_origin(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    wf_exec_id: WorkflowExecutionID | None = Query(default=None),
    session_id: uuid.UUID | None = Query(default=None),
) -> TriggerRunOrigin | None:
    """The trigger that started a workflow run or an agent session, or null.

    Give exactly one of ``wf_exec_id`` (needs ``workflow:read``) and
    ``session_id`` (needs ``agent:read``).
    """
    service = TriggerRunsService(session, role)
    try:
        return await service.run_origin(wf_exec_id=wf_exec_id, session_id=session_id)
    except ValueError as e:
        raise _bad_request(e) from e


@router.get("/{trigger_id}/runs")
@require_scope("trigger:read")
async def list_trigger_runs(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    trigger_id: uuid.UUID,
    limit: LimitQuery = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = Query(default=None),
) -> TriggerRunsPage:
    """The runs the trigger started, open runs first, then by close time.

    Workflow runs need ``workflow:read`` and agent runs ``agent:read``.
    """
    service = TriggerRunsService(session, role)
    try:
        return await service.list_runs(
            trigger_id, CursorPaginationParams(limit=limit, cursor=cursor)
        )
    except ValueError as e:
        raise _bad_request(e) from e


@router.get("/{trigger_id}/stopped-events")
@require_scope("trigger:read")
async def list_trigger_stopped_events(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    trigger_id: uuid.UUID,
    limit: LimitQuery = config.TRACECAT__LIMIT_DEFAULT,
    cursor: str | None = Query(default=None),
) -> TriggerStoppedEventsPage:
    """The trigger's events that stopped retrying, newest first.

    Only ``object_storage`` triggers queue events; other kinds list none.
    Also needs ``workflow:read`` and ``agent:read``.
    """
    service = TriggerRunsService(session, role)
    try:
        return await service.list_stopped_events(
            trigger_id, CursorPaginationParams(limit=limit, cursor=cursor)
        )
    except ValueError as e:
        raise _bad_request(e) from e
