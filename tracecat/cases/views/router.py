"""Public API for saved case views."""

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.cases.views.schemas import (
    CaseViewCreate,
    CaseViewPinUpdate,
    CaseViewRead,
    CaseViewUpdate,
)
from tracecat.cases.views.service import (
    CaseViewNotFoundError,
    CaseViewPermissionError,
    CaseViewsService,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, PageParams

router = APIRouter(prefix="/case-views", tags=["case-views"])


def raise_case_view_error(exc: Exception) -> NoReturn:
    """Map a view service error to its HTTP status."""
    match exc:
        case CaseViewNotFoundError():
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        case CaseViewPermissionError():
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        case TracecatValidationError():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        case _:
            raise exc


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
    return CaseViewsService(session, role).list_builtins()


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("case:read")
async def create_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, params: CaseViewCreate
) -> CaseViewRead:
    """Save a view. Sharing it with the workspace requires case:update."""
    try:
        return await CaseViewsService(session, role).create_view(params)
    except (CaseViewPermissionError, TracecatValidationError) as exc:
        raise_case_view_error(exc)


@router.get("/{view_id}")
@require_scope("case:read")
async def get_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, view_id: str
) -> CaseViewRead:
    """Get a saved or built-in view, including references to deleted tags."""
    try:
        return await CaseViewsService(session, role).get_view(view_id)
    except (CaseViewNotFoundError, TracecatValidationError) as exc:
        raise_case_view_error(exc)


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
    except (
        CaseViewNotFoundError,
        CaseViewPermissionError,
        TracecatValidationError,
    ) as exc:
        raise_case_view_error(exc)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("case:read")
async def delete_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, view_id: str
) -> None:
    """Delete a view you own, or a shared view if you have case:update."""
    try:
        await CaseViewsService(session, role).delete_view(view_id)
    except (
        CaseViewNotFoundError,
        CaseViewPermissionError,
        TracecatValidationError,
    ) as exc:
        raise_case_view_error(exc)


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
    except (
        CaseViewNotFoundError,
        CaseViewPermissionError,
        TracecatValidationError,
    ) as exc:
        raise_case_view_error(exc)


@router.delete("/{view_id}/pin", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("case:read")
async def unpin_case_view(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, view_id: str
) -> None:
    """Remove a view from your sidebar."""
    try:
        await CaseViewsService(session, role).unpin_view(view_id)
    except (
        CaseViewNotFoundError,
        CaseViewPermissionError,
        TracecatValidationError,
    ) as exc:
        raise_case_view_error(exc)
