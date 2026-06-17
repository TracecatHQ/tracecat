"""Inbox service backed by a single notification provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.inbox.schemas import InboxItemRead
    from tracecat.inbox.types import InboxGroup, InboxProvider
    from tracecat.pagination import CursorPaginatedResponse


class InboxService(BaseWorkspaceService):
    """Service that delegates to a single inbox provider.

    The inbox is currently sourced entirely from agent runs. The provider does
    its own SQL keyset pagination, grouping, search, and sorting, so the service
    is a thin pass-through.
    """

    service_name = "inbox"

    def __init__(
        self,
        session: AsyncSession,
        role: Role,
        provider: InboxProvider,
    ):
        super().__init__(session, role)
        self.provider = provider

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
        """List inbox items with cursor-based pagination."""
        return await self.provider.list_items(
            limit=limit,
            cursor=cursor,
            reverse=reverse,
            order_by=order_by,
            sort=sort,
            search=search,
            group=group,
        )

    async def count_pending_items(self) -> int:
        """Count pending inbox items that require attention."""
        return await self.provider.count_pending_items()
