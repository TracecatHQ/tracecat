from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.service import InboxService
from tracecat.inbox.types import InboxGroup, InboxItemStatus, InboxItemType
from tracecat.pagination import CursorPaginatedResponse


class _InboxProvider:
    def __init__(
        self,
        response: CursorPaginatedResponse[InboxItemRead],
        *,
        pending_count: int = 0,
    ) -> None:
        self.response = response
        self.pending_count = pending_count
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "limit": limit,
                "cursor": cursor,
                "reverse": reverse,
                "order_by": order_by,
                "sort": sort,
                "search": search,
                "group": group,
            }
        )
        return self.response

    async def count_pending_items(self) -> int:
        return self.pending_count


def _role() -> Role:
    return Role(
        type="service",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        service_id="tracecat-runner",
        scopes=frozenset({"inbox:read"}),
    )


def _inbox_item(title: str, created_at: datetime) -> InboxItemRead:
    return InboxItemRead(
        id=uuid.uuid4(),
        type=InboxItemType.AGENT_RUN,
        title=title,
        preview="Agent session",
        status=InboxItemStatus.COMPLETED,
        unread=False,
        created_at=created_at,
        updated_at=created_at,
        workflow=None,
        created_by=None,
        source_id=uuid.uuid4(),
        source_type="agent_session",
        metadata={},
    )


def _page(
    items: Sequence[InboxItemRead],
    *,
    next_cursor: str | None = None,
    prev_cursor: str | None = None,
    has_more: bool = False,
    has_previous: bool = False,
) -> CursorPaginatedResponse[InboxItemRead]:
    return CursorPaginatedResponse(
        items=list(items),
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=has_more,
        has_previous=has_previous,
        total_estimate=None,
    )


def _service(provider: _InboxProvider) -> InboxService:
    return InboxService(cast(AsyncSession, object()), _role(), provider)


@pytest.mark.anyio
async def test_list_items_passes_arguments_through_to_provider() -> None:
    item = _inbox_item("only", datetime(2026, 1, 1, tzinfo=UTC))
    provider = _InboxProvider(_page([item], has_more=True, next_cursor="next"))
    service = _service(provider)

    page = await service.list_items(
        limit=5,
        cursor="cur",
        reverse=True,
        order_by="updated_at",
        sort="asc",
        search="needle",
        group=InboxGroup.COMPLETED,
    )

    assert page is provider.response
    assert page.items == [item]
    assert provider.calls == [
        {
            "limit": 5,
            "cursor": "cur",
            "reverse": True,
            "order_by": "updated_at",
            "sort": "asc",
            "search": "needle",
            "group": InboxGroup.COMPLETED,
        }
    ]


@pytest.mark.anyio
async def test_count_pending_items_delegates_to_provider() -> None:
    provider = _InboxProvider(_page([]), pending_count=7)
    service = _service(provider)

    assert await service.count_pending_items() == 7
