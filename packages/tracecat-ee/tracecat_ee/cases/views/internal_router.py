"""Read-only case view routes for the registry SDK (workflow actions)."""

from fastapi import APIRouter, Query

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.cases.errors import CASE_REQUEST_ERRORS, case_http_error
from tracecat.db.dependencies import AsyncDBSession
from tracecat.pagination import CursorPaginatedResponse, PageParams
from tracecat_ee.cases.views.schemas import CaseViewDetail, CaseViewRead
from tracecat_ee.cases.views.service import CaseViewsService

router = APIRouter(
    prefix="/internal/case-views",
    tags=["internal-case-views"],
    include_in_schema=False,
)


@router.get("")
@require_scope("case:read")
async def executor_list_case_views(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(None),
) -> CursorPaginatedResponse[CaseViewRead]:
    """List shared saved views. Workflows have no user, so no personal views."""
    return await CaseViewsService(session, role).list_views(
        PageParams(limit=limit, cursor=cursor)
    )


@router.get("/builtins")
@require_scope("case:read")
async def executor_list_builtin_case_views(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession
) -> list[CaseViewRead]:
    """List the built-in views."""
    return await CaseViewsService(session, role).list_builtins()


@router.get("/{view_id}")
@require_scope("case:read")
async def executor_get_case_view(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, view_id: str
) -> CaseViewDetail:
    """Get one shared or built-in view."""
    try:
        return await CaseViewsService(session, role).get_view(view_id)
    except CASE_REQUEST_ERRORS as exc:
        raise case_http_error(exc) from exc
