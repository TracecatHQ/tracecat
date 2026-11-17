"""Public API for saved case views."""

from fastapi import APIRouter, Query, status

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.cases.errors import CASE_REQUEST_ERRORS, case_http_error
from tracecat.db.dependencies import AsyncDBSession
from tracecat.pagination import CursorPaginatedResponse, PageParams
from tracecat_ee.cases.views.schemas import (
    CaseViewCreate,
    CaseViewDetail,
    CaseViewPinUpdate,
    CaseViewRead,
    CaseViewUpdate,
)
from tracecat_ee.cases.views.service import CaseViewsService

router = APIRouter(prefix="/case-views", tags=["case-views"])


@router.get("")
@require_scope("case:read")
async def list_case_views(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(None, description="Continuation cursor"),
    pinned: bool = Query(
        False, description="Only views the requesting user pinned, in pin order"
    ),
) -> CursorPaginatedResponse[CaseViewRead]:
    """List saved views: shared ones and the requesting user's personal ones."""
    service = CaseViewsService(session, role)
    return await service.list_views(
        PageParams(limit=limit, cursor=cursor), pinned_only=pinned
    )


@router.get("/builtins")
@require_scope("case:read")
async def list_builtin_case_views(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> list[CaseViewRead]:
    """List the views every workspace has, such as Assigned to me."""
    return await CaseViewsService(session, role).list_builtins()


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("case:read")
async def create_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, params: CaseViewCreate
) -> CaseViewRead:
    """Save a view. Sharing it with the workspace requires case:update."""
    try:
        return await CaseViewsService(session, role).create_view(params)
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc


@router.get("/{view_id}")
@require_scope("case:read")
async def get_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, view_id: str
) -> CaseViewDetail:
    """Get a saved or built-in view, including references to deleted tags."""
    try:
        return await CaseViewsService(session, role).get_view(view_id)
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc


@router.patch("/{view_id}")
@require_scope("case:read")
async def update_case_view(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    view_id: str,
    params: CaseViewUpdate,
) -> CaseViewRead:
    """Change a view you own, or a shared view if you have case:update."""
    try:
        return await CaseViewsService(session, role).update_view(view_id, params)
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("case:read")
async def delete_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, view_id: str
) -> None:
    """Delete a view you own, or a shared view if you have case:update."""
    try:
        await CaseViewsService(session, role).delete_view(view_id)
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc


@router.put("/{view_id}/pin")
@require_scope("case:read")
async def pin_case_view(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    view_id: str,
    params: CaseViewPinUpdate,
) -> CaseViewRead:
    """Show a saved view in your sidebar."""
    try:
        return await CaseViewsService(session, role).pin_view(
            view_id, position=params.position
        )
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc


@router.delete("/{view_id}/pin", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("case:read")
async def unpin_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, view_id: str
) -> None:
    """Remove a view from your sidebar."""
    try:
        await CaseViewsService(session, role).unpin_view(view_id)
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc
