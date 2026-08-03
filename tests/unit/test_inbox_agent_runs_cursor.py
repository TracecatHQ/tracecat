"""Cursor-contract tests for the agent runs inbox provider.

The provider paginates on either ``created_at`` or ``updated_at``. Applying a
cursor minted for one column to a scan ordered by the other resumes at the
wrong keyset position, which silently skips or repeats sessions, so both the
ungrouped and grouped decode paths must reject a cross-sort cursor.

These exercise cursor validation only, which happens before any query runs, so
the session is a stub.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from tracecat_ee.inbox.providers.agent_runs import AgentRunsInboxProvider

from tracecat.auth.types import Role
from tracecat.inbox.types import InboxGroup
from tracecat.pagination import BaseCursorPaginator

pytestmark = pytest.mark.anyio


@pytest.fixture
def provider() -> AgentRunsInboxProvider:
    role = Role(
        type="user", workspace_id=uuid4(), user_id=uuid4(), service_id="tracecat-api"
    )
    return AgentRunsInboxProvider(session=MagicMock(), role=role)


def _cursor(sort_column: str) -> str:
    return BaseCursorPaginator.encode_cursor(
        uuid4(),
        sort_column=sort_column,
        sort_value=datetime(2026, 1, 1, tzinfo=UTC),
    )


async def test_list_items_rejects_a_cursor_from_a_different_sort(
    provider: AgentRunsInboxProvider,
) -> None:
    with pytest.raises(ValueError, match="sorts by 'updated_at'"):
        await provider.list_items(cursor=_cursor("created_at"), order_by="updated_at")


async def test_grouped_list_items_rejects_a_cursor_from_a_different_sort(
    provider: AgentRunsInboxProvider,
) -> None:
    with pytest.raises(ValueError, match="sorts by 'updated_at'"):
        await provider.list_items(
            cursor=_cursor("created_at"),
            order_by="updated_at",
            group=InboxGroup.RUNNING,
        )


@pytest.mark.parametrize("group", [None, InboxGroup.RUNNING])
async def test_list_items_rejects_a_sortless_cursor(
    provider: AgentRunsInboxProvider, group: InboxGroup | None
) -> None:
    """Cursors predating sort-aware pagination carry no sort column."""
    cursor = BaseCursorPaginator.encode_cursor(uuid4())

    with pytest.raises(ValueError, match="Cursor was created for sort column None"):
        await provider.list_items(cursor=cursor, order_by="created_at", group=group)


@pytest.mark.parametrize("group", [None, InboxGroup.RUNNING])
async def test_list_items_rejects_a_non_datetime_sort_value(
    provider: AgentRunsInboxProvider, group: InboxGroup | None
) -> None:
    """Both columns are timestamps; anything else cannot filter the keyset."""
    cursor = BaseCursorPaginator.encode_cursor(
        uuid4(), sort_column="created_at", sort_value=7
    )

    with pytest.raises(ValueError, match="wrong type"):
        await provider.list_items(cursor=cursor, order_by="created_at", group=group)
