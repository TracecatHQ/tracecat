"""Inbox API router."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.inbox.dependencies import get_inbox_providers
from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.service import InboxService
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse

router = APIRouter(prefix="/inbox", tags=["inbox"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("/items")
async def list_items(
    role: WorkspaceUser,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
    order_by: str | None = Query(
        default=None,
        description="Column name to order by (created_at, updated_at, status)",
    ),
    sort: Literal["asc", "desc"] | None = Query(
        default=None, description="Sort direction (asc or desc)"
    ),
) -> CursorPaginatedResponse[InboxItemRead]:
    """List inbox items with cursor-based pagination.

    Supports sorting by created_at, updated_at, or status.
    Default sort is by created_at descending.
    """
    providers = get_inbox_providers(session, role)
    service = InboxService(session, role, providers)

    try:
        return await service.list_items(
            limit=limit,
            cursor=cursor,
            reverse=reverse,
            order_by=order_by,
            sort=sort,
        )
    except ValueError as e:
        logger.warning(f"Invalid request for list inbox items: {e}")

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
