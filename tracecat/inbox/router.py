"""Inbox API router."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.inbox.dependencies import get_inbox_provider
from tracecat.inbox.schemas import InboxItemRead, InboxPendingCount
from tracecat.inbox.service import InboxService
from tracecat.inbox.types import InboxGroup
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("/items/pending-count")
@require_scope("inbox:read")
async def get_pending_count(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> InboxPendingCount:
    """Get the number of pending inbox items that require attention."""
    provider = get_inbox_provider(session, role)
    if provider is None:
        return InboxPendingCount(count=0)

    service = InboxService(session, role, provider)
    return InboxPendingCount(count=await service.count_pending_items())


@router.get("/items")
@require_scope("inbox:read")
async def list_items(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
    order_by: Literal["created_at", "updated_at"] | None = Query(
        default=None,
        description="Column name to order by (created_at, updated_at)",
    ),
    sort: Literal["asc", "desc"] | None = Query(
        default=None, description="Sort direction (asc or desc)"
    ),
    search: str | None = Query(
        default=None,
        max_length=200,
        description="Case-insensitive search on item title",
    ),
    group: InboxGroup | None = Query(
        default=None,
        description="Filter items to a single display group",
    ),
) -> CursorPaginatedResponse[InboxItemRead]:
    """List inbox items with cursor-based pagination.

    Supports sorting by created_at or updated_at.
    Default sort is by created_at descending.
    """
    provider = get_inbox_provider(session, role)
    if provider is None:
        return CursorPaginatedResponse(
            items=[],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
            total_estimate=0,
        )

    service = InboxService(session, role, provider)

    search = search.strip() or None if search else None

    try:
        return await service.list_items(
            limit=limit,
            cursor=cursor,
            reverse=reverse,
            order_by=order_by,
            sort=sort,
            search=search,
            group=group,
        )
    except ValueError as e:
        logger.warning(f"Invalid request for list inbox items: {e}")

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
