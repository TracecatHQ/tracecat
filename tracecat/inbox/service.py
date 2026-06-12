"""Inbox service for aggregating notification items from multiple providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.types import InboxGroup, InboxItemStatus
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
        search: str | None = None,
        group: InboxGroup | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List inbox items with cursor-based pagination.

        With a single provider, delegates pagination directly so the provider's
        SQL keyset pagination is used end-to-end. With multiple providers,
        falls back to in-memory aggregation.
        """
        if len(self.providers) == 1:
            return await self.providers[0].list_items(
                limit=limit,
                cursor=cursor,
                reverse=reverse,
                order_by=order_by,
                sort=sort,
                search=search,
                group=group,
            )

        # Multi-provider: aggregate in memory.
        # Grouped queries pass cursor straight through; each provider owns its
        # own scan-position cursors and returns the correct page directly.
        if group is not None:
            all_items: list[InboxItemRead] = []
            next_cursor: str | None = None
            prev_cursor: str | None = None
            has_more = False
            has_previous = False
            for provider in self.providers:
                provider_response = await provider.list_items(
                    limit=limit,
                    cursor=cursor,
                    reverse=reverse,
                    order_by=order_by,
                    sort=sort,
                    search=search,
                    group=group,
                )
                all_items.extend(provider_response.items)
                if provider_response.has_more:
                    has_more = True
                    next_cursor = provider_response.next_cursor
                if provider_response.has_previous:
                    has_previous = True
                    prev_cursor = provider_response.prev_cursor
            return CursorPaginatedResponse(
                items=all_items,
                next_cursor=next_cursor,
                prev_cursor=prev_cursor,
                has_more=has_more,
                has_previous=has_previous,
                total_estimate=None,
            )

        all_items_ungrouped: list[InboxItemRead] = []
        providers_have_more = False

        # Decode cursor to get target item ID if present
        cursor_id: str | None = None
        if cursor:
            cursor_data = self.decode_cursor(cursor)
            cursor_id = cursor_data.id

        # Fetch a larger window per provider so the cursor item is likely within
        # the fetched set after merging. Known limitation: very large inboxes
        # should use a unified table instead.
        initial_fetch_limit = limit * 10 if cursor_id else limit * 2

        for provider in self.providers:
            provider_response = await provider.list_items(
                limit=initial_fetch_limit,
                cursor=None,
                reverse=reverse,
                order_by=order_by,
                sort=sort,
                search=search,
                group=None,
            )
            all_items_ungrouped.extend(provider_response.items)
            providers_have_more = providers_have_more or provider_response.has_more

        # Sort merged results
        sort_desc = sort != "asc"
        if order_by == "created_at" or order_by is None:
            all_items_ungrouped.sort(
                key=lambda x: x.created_at.timestamp(),
                reverse=sort_desc,
            )
        elif order_by == "updated_at":
            all_items_ungrouped.sort(
                key=lambda x: x.updated_at.timestamp(),
                reverse=sort_desc,
            )
        elif order_by == "status":
            all_items_ungrouped.sort(
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
            for i, item in enumerate(all_items_ungrouped):
                if str(item.id) == cursor_id:
                    start_idx = i + 1 if not reverse else i
                    break

        # Slice
        end_idx = start_idx
        if reverse:
            start_idx = max(0, end_idx - limit)
            items = all_items_ungrouped[start_idx:end_idx]
            items.reverse()
        else:
            items = all_items_ungrouped[start_idx : start_idx + limit]

        # Determine pagination state
        if reverse:
            has_more = start_idx > 0
            has_previous = end_idx < len(all_items_ungrouped) or providers_have_more
        else:
            has_more = (
                start_idx + limit < len(all_items_ungrouped) or providers_have_more
            )
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
            total_estimate=len(all_items_ungrouped),
        )

    async def count_pending_items(self) -> int:
        """Count pending inbox items across all configured providers."""
        count = 0
        for provider in self.providers:
            count += await provider.count_pending_items()
        return count
