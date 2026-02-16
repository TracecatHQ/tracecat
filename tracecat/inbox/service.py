"""Inbox service for aggregating notification items from multiple providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.types import InboxItemStatus
from tracecat.pagination import BaseCursorPaginator, CursorPaginatedResponse
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.inbox.types import InboxProvider


class InboxService(BaseWorkspaceService, BaseCursorPaginator):
    """Service for aggregating inbox items from multiple providers."""

    service_name = "inbox"

    def __init__(
        self,
        session: AsyncSession,
        role: Role,
        providers: list[InboxProvider] | None = None,
    ):
        BaseWorkspaceService.__init__(self, session, role)
        BaseCursorPaginator.__init__(self, session)
        self.providers = providers or []

    async def list_items(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        reverse: bool = False,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List inbox items with cursor-based pagination.

        For the initial implementation, this delegates pagination to providers
        and merges results. Future optimization could use a materialized view
        or unified table for more efficient cross-provider pagination.
        """
        # For now, use simple aggregation with in-memory pagination
        # This works well for small-medium inbox sizes
        # TODO: Optimize for large inboxes with cross-provider cursor pagination

        all_items: list[InboxItemRead] = []

        # Decode cursor to get target item ID if present
        cursor_id: str | None = None
        if cursor:
            cursor_data = self.decode_cursor(cursor)
            cursor_id = cursor_data.id

        # Initial fetch - get enough items to likely include cursor position.
        # For cross-provider aggregation, we fetch a larger window to increase
        # the likelihood of including the cursor item. This is a known limitation
        # of in-memory aggregation; very large inboxes may need a unified table.
        # Fetch more when we have a cursor to search for.
        initial_fetch_limit = limit * 10 if cursor_id else limit * 2

        for provider in self.providers:
            # Fetch items without cursor - handle pagination at aggregate level
            provider_response = await provider.list_items(
                limit=initial_fetch_limit,
                cursor=None,
                reverse=reverse,
                order_by=order_by,
                sort=sort,
            )
            all_items.extend(provider_response.items)

        # Sort all items
        sort_desc = sort != "asc"
        if order_by == "created_at" or order_by is None:
            all_items.sort(
                key=lambda x: x.created_at.timestamp(),
                reverse=sort_desc,
            )
        elif order_by == "updated_at":
            all_items.sort(
                key=lambda x: x.updated_at.timestamp(),
                reverse=sort_desc,
            )
        elif order_by == "status":
            # Status priority: pending first (asc) or completed first (desc)
            # Secondary sort by created_at in the same direction
            all_items.sort(
                key=lambda x: (
                    x.status != InboxItemStatus.PENDING,
                    x.created_at.timestamp()
                    if sort_desc
                    else -x.created_at.timestamp(),
                ),
                reverse=sort_desc,
            )

        # Find cursor position in merged results
        start_idx = 0
        if cursor_id:
            for i, item in enumerate(all_items):
                if item.id == cursor_id:
                    start_idx = i + 1 if not reverse else i
                    break

        # Slice items
        if reverse:
            end_idx = start_idx
            start_idx = max(0, end_idx - limit)
            items = all_items[start_idx:end_idx]
            items.reverse()
        else:
            items = all_items[start_idx : start_idx + limit]

        # Determine pagination state
        has_more = start_idx + limit < len(all_items)
        has_previous = start_idx > 0

        # Generate cursors
        next_cursor = None
        prev_cursor = None

        if items:
            if has_more:
                last_item = items[-1]
                next_cursor = self.encode_cursor(
                    id=last_item.id,
                    sort_column=order_by or "created_at",
                    sort_value=getattr(last_item, order_by or "created_at"),
                )
            if has_previous:
                first_item = items[0]
                prev_cursor = self.encode_cursor(
                    id=first_item.id,
                    sort_column=order_by or "created_at",
                    sort_value=getattr(first_item, order_by or "created_at"),
                )

        return CursorPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=len(all_items),
        )
