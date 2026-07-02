from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.inbox.providers.agent_runs import AgentRunsInboxProvider

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.inbox.schemas import InboxItemRead
from tracecat.inbox.types import InboxGroup, InboxItemStatus, InboxItemType
from tracecat.pagination import BaseCursorPaginator, CursorPaginatedResponse


class _ScalarResult:
    def __init__(self, items: Sequence[AgentSession]) -> None:
        self._items = list(items)

    def all(self) -> list[AgentSession]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: Sequence[AgentSession]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _RecordingSession:
    def __init__(self, batches: Sequence[Sequence[AgentSession]] | None = None) -> None:
        self._batches = [list(batch) for batch in batches or []]
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.statements.append(stmt)
        if self._batches:
            return _ExecuteResult(self._batches.pop(0))
        return _ExecuteResult([])


def _role(workspace_id: uuid.UUID | None = None) -> Role:
    workspace_id = workspace_id or uuid.uuid4()
    return Role(
        type="service",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=workspace_id,
        service_id="tracecat-runner",
        scopes=frozenset({"inbox:read"}),
    )


def _agent_session(
    title: str,
    created_at: datetime,
    *,
    workspace_id: uuid.UUID,
) -> AgentSession:
    return AgentSession(
        id=uuid.uuid4(),
        title=title,
        workspace_id=workspace_id,
        entity_type="workflow",
        entity_id=uuid.uuid4(),
        harness_type=HarnessType.CLAUDE_CODE,
        created_at=created_at,
        updated_at=created_at,
    )


def _items_for_sessions(sessions: Sequence[AgentSession]) -> list[InboxItemRead]:
    return [
        InboxItemRead(
            id=session.id,
            type=InboxItemType.AGENT_RUN,
            title=session.title,
            preview="Agent session",
            status=InboxItemStatus.COMPLETED,
            unread=False,
            created_at=session.created_at,
            updated_at=session.updated_at,
            workflow=None,
            created_by=None,
            source_id=session.id,
            source_type="agent_session",
            metadata={},
        )
        for session in sessions
    ]


@pytest.mark.anyio
async def test_grouped_list_items_passes_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AgentRunsInboxProvider(
        cast(AsyncSession, _RecordingSession()),
        _role(),
    )
    expected = CursorPaginatedResponse[InboxItemRead](
        items=[],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=None,
    )
    grouped = AsyncMock(return_value=expected)
    monkeypatch.setattr(provider, "_list_items_grouped", grouped)

    page = await provider.list_items(
        limit=10,
        cursor="cursor",
        reverse=True,
        group=InboxGroup.COMPLETED,
    )

    assert page is expected
    grouped.assert_awaited_once()
    await_args = grouped.await_args
    assert await_args is not None
    assert await_args.kwargs["reverse"] is True


@pytest.mark.anyio
async def test_grouped_reverse_uses_newer_keyset_and_ascending_order() -> None:
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())
    cursor = BaseCursorPaginator.encode_cursor(
        uuid.uuid4(),
        sort_column="created_at",
        sort_value=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await provider._list_items_grouped(
        limit=10,
        cursor=cursor,
        reverse=True,
        order_by=None,
        sort=None,
        search=None,
        group=InboxGroup.COMPLETED,
    )

    compiled = str(session.statements[-1].compile())
    assert "agent_session.created_at >" in compiled
    assert "agent_session.id >" in compiled
    assert "ORDER BY agent_session.created_at ASC, agent_session.id ASC" in compiled


@pytest.mark.anyio
async def test_grouped_reverse_page_uses_canonical_order_and_swapped_cursors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    raw_reverse_rows = [
        _agent_session(
            "nearer", base_time + timedelta(minutes=1), workspace_id=workspace_id
        ),
        _agent_session(
            "middle", base_time + timedelta(minutes=2), workspace_id=workspace_id
        ),
        _agent_session(
            "farther", base_time + timedelta(minutes=3), workspace_id=workspace_id
        ),
    ]
    session = _RecordingSession([raw_reverse_rows])
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role(workspace_id))

    async def classify(
        sessions: Sequence[AgentSession],
        *,
        group: InboxGroup,
    ) -> dict[uuid.UUID, InboxGroup]:
        return {session.id: group for session in sessions}

    async def enrich(sessions: Sequence[AgentSession]) -> list[InboxItemRead]:
        return _items_for_sessions(sessions)

    monkeypatch.setattr(provider, "_classify_sessions_for_group", classify)
    monkeypatch.setattr(provider, "_enrich_sessions", enrich)
    cursor = BaseCursorPaginator.encode_cursor(
        uuid.uuid4(),
        sort_column="created_at",
        sort_value=base_time,
    )

    page = await provider._list_items_grouped(
        limit=2,
        cursor=cursor,
        reverse=True,
        order_by=None,
        sort=None,
        search=None,
        group=InboxGroup.COMPLETED,
    )

    assert [item.id for item in page.items] == [
        raw_reverse_rows[1].id,
        raw_reverse_rows[0].id,
    ]
    assert page.next_cursor == BaseCursorPaginator.encode_cursor(
        raw_reverse_rows[0].id,
        sort_column="created_at",
        sort_value=raw_reverse_rows[0].created_at,
    )
    assert page.prev_cursor == BaseCursorPaginator.encode_cursor(
        raw_reverse_rows[1].id,
        sort_column="created_at",
        sort_value=raw_reverse_rows[1].created_at,
    )
    assert page.has_more is True
    assert page.has_previous is True


@pytest.mark.anyio
async def test_grouped_reverse_empty_page_does_not_advertise_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty reverse page must report has_more=False, not a dead cursor.

    Reverse pagination once hardcoded ``has_more = cursor is not None``, which
    yielded has_more=true with next_cursor=None on an empty page and enabled a
    non-functional "show more" control.
    """
    session = _RecordingSession([[]])
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())
    cursor = BaseCursorPaginator.encode_cursor(
        uuid.uuid4(),
        sort_column="created_at",
        sort_value=datetime(2026, 1, 1, tzinfo=UTC),
    )

    page = await provider._list_items_grouped(
        limit=10,
        cursor=cursor,
        reverse=True,
        order_by=None,
        sort=None,
        search=None,
        group=InboxGroup.COMPLETED,
    )

    assert page.items == []
    assert page.next_cursor is None
    assert page.has_more is False


@pytest.mark.anyio
async def test_ungrouped_reverse_scans_newer_keyset_ascending() -> None:
    """Ungrouped reverse pagination must flip the scan, like the grouped path.

    A reverse page selects rows newer than the cursor; scanning descending then
    truncating to the limit would keep the newest rows and skip the ones
    immediately adjacent to the cursor. The scan must run ascending so the LIMIT
    keeps the adjacent rows.
    """
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())
    cursor = BaseCursorPaginator.encode_cursor(
        uuid.uuid4(),
        sort_column="created_at",
        sort_value=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await provider.list_items(limit=10, cursor=cursor, reverse=True)

    compiled = str(session.statements[-1].compile())
    assert "agent_session.created_at >" in compiled
    assert "agent_session.id >" in compiled
    assert "ORDER BY agent_session.created_at ASC, agent_session.id ASC" in compiled


@pytest.mark.anyio
async def test_ungrouped_reverse_returns_rows_adjacent_to_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse page must contain the rows immediately before the cursor.

    For a desc list [120, 110, 100, 90, 80], paging backwards from the cursor at
    80 with limit=2 must return [100, 90] in display order, not [110, 120].
    """
    workspace_id = uuid.uuid4()
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    # Rows newer than the cursor, returned by the ascending scan in age order:
    # 90 (nearest the cursor), then 100, then 110 (the +1 lookahead row).
    scan_rows = [
        _agent_session(
            "90", base_time + timedelta(minutes=1), workspace_id=workspace_id
        ),
        _agent_session(
            "100", base_time + timedelta(minutes=2), workspace_id=workspace_id
        ),
        _agent_session(
            "110", base_time + timedelta(minutes=3), workspace_id=workspace_id
        ),
    ]
    session = _RecordingSession([scan_rows])
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role(workspace_id))

    async def enrich(sessions: Sequence[AgentSession]) -> list[InboxItemRead]:
        return _items_for_sessions(sessions)

    monkeypatch.setattr(provider, "_enrich_sessions", enrich)
    cursor = BaseCursorPaginator.encode_cursor(
        uuid.uuid4(),
        sort_column="created_at",
        sort_value=base_time,
    )

    page = await provider.list_items(limit=2, cursor=cursor, reverse=True)

    # Display order is the requested desc sort: newest first.
    assert [item.title for item in page.items] == ["100", "90"]
    assert page.has_more is True
    assert page.next_cursor == BaseCursorPaginator.encode_cursor(
        scan_rows[0].id,
        sort_column="created_at",
        sort_value=scan_rows[0].created_at,
    )
    assert page.prev_cursor == BaseCursorPaginator.encode_cursor(
        scan_rows[1].id,
        sort_column="created_at",
        sort_value=scan_rows[1].created_at,
    )


@pytest.mark.anyio
async def test_classify_rejected_only_non_workflow_session_lands_in_error_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any session whose only signal is a rejected approval groups under ERROR.

    ``_enrich_sessions`` renders such a session FAILED, so classification must
    place it in ERROR rather than COMPLETED even when it is not workflow-backed.
    """
    workspace_id = uuid.uuid4()
    provider = AgentRunsInboxProvider(
        cast(AsyncSession, _RecordingSession()), _role(workspace_id)
    )
    rejected = AgentSession(
        id=uuid.uuid4(),
        title="rejected",
        workspace_id=workspace_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        harness_type=HarnessType.CLAUDE_CODE,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def fetch_counts(
        session_ids: Sequence[uuid.UUID],
        status: Any,
    ) -> dict[uuid.UUID, int]:
        from tracecat.agent.approvals.enums import ApprovalStatus

        if status == ApprovalStatus.REJECTED:
            return {rejected.id: 1}
        return {}

    async def resolve_statuses(
        sessions: Sequence[AgentSession],
    ) -> dict[uuid.UUID, Any]:
        return {}

    monkeypatch.setattr(provider, "_fetch_approval_counts", fetch_counts)
    monkeypatch.setattr(provider, "_resolve_live_statuses", resolve_statuses)

    classifications = await provider._classify_sessions_for_group(
        [rejected], group=InboxGroup.ERROR
    )

    assert classifications[rejected.id] is InboxGroup.ERROR


@pytest.mark.anyio
async def test_review_required_group_does_not_restrict_approval_entity_type() -> None:
    """Pending approvals are inbox-relevant regardless of session entity type."""
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    await provider._list_items_grouped(
        limit=10,
        cursor=None,
        reverse=False,
        order_by=None,
        sort=None,
        search=None,
        group=InboxGroup.REVIEW_REQUIRED,
    )

    compiled = str(session.statements[-1].compile())
    assert "approval.status =" in compiled
    assert "agent_session.entity_type IN" not in compiled


@pytest.mark.anyio
async def test_ungrouped_list_applies_entity_type_and_date_filters() -> None:
    """entity_type and date filters narrow the keyset selection in SQL.

    Filtering in SQL (rather than client-side on a server-chosen page) is what
    keeps groups from looking short/empty and keeps has_more honest.
    """
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    created_after = datetime(2026, 1, 1, tzinfo=UTC)
    updated_after = datetime(2026, 2, 1, tzinfo=UTC)
    await provider.list_items(
        limit=10,
        entity_type=AgentSessionEntity.CASE,
        created_after=created_after,
        updated_after=updated_after,
    )

    compiled = str(session.statements[-1].compile())
    assert "agent_session.entity_type =" in compiled
    assert "agent_session.created_at >=" in compiled
    assert "agent_session.updated_at >=" in compiled


@pytest.mark.anyio
async def test_grouped_list_applies_entity_type_and_date_filters() -> None:
    """Grouped scan inherits the same SQL filters via the shared base query."""
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    await provider._list_items_grouped(
        limit=10,
        cursor=None,
        reverse=False,
        order_by=None,
        sort=None,
        search=None,
        group=InboxGroup.COMPLETED,
        entity_type=AgentSessionEntity.WORKFLOW,
        created_after=datetime(2026, 1, 1, tzinfo=UTC),
        updated_after=datetime(2026, 2, 1, tzinfo=UTC),
    )

    compiled = str(session.statements[-1].compile())
    assert "agent_session.entity_type =" in compiled
    assert "agent_session.created_at >=" in compiled
    assert "agent_session.updated_at >=" in compiled


@pytest.mark.anyio
async def test_enrich_scopes_workflow_lookup_to_workspace() -> None:
    """Workflow metadata enrichment must be scoped to the caller's workspace.

    A session's ``entity_id`` can reference a workflow ID in another workspace;
    without a workspace predicate the workflow title/alias of that other
    workspace would leak into this workspace's inbox.
    """
    workspace_id = uuid.uuid4()
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role(workspace_id))

    agent_session = _agent_session(
        "leaky", datetime(2026, 1, 1, tzinfo=UTC), workspace_id=workspace_id
    )

    await provider._enrich_sessions([agent_session])

    workflow_statements = [
        str(stmt.compile())
        for stmt in session.statements
        if "FROM workflow" in str(stmt.compile())
    ]
    assert workflow_statements, "expected a workflow enrichment query"
    for compiled in workflow_statements:
        assert "workflow.workspace_id =" in compiled


@pytest.mark.anyio
async def test_search_scopes_workflow_match_to_workspace() -> None:
    """Search-by-workflow-name must be scoped to the caller's workspace.

    The workflow-title/alias EXISTS subquery joins on entity_id; without a
    workspace predicate a user could match (and thus infer) another workspace's
    workflow names through search results.
    """
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    await provider.list_items(limit=10, search="secret")

    compiled = str(session.statements[-1].compile())
    assert "workflow.workspace_id =" in compiled


@pytest.mark.anyio
async def test_ungrouped_malformed_cursor_raises_value_error() -> None:
    """A malformed cursor must raise ValueError so the router returns 400.

    Silently ignoring the bad cursor would fall back to the first page and
    return plausible-but-wrong data with misleading pagination flags.
    """
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    with pytest.raises(ValueError):
        await provider.list_items(limit=10, cursor="not-base64")


@pytest.mark.anyio
async def test_grouped_malformed_cursor_raises_value_error() -> None:
    """The grouped scan path must also reject malformed cursors with ValueError."""
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    with pytest.raises(ValueError):
        await provider._list_items_grouped(
            limit=10,
            cursor="not-base64",
            reverse=False,
            order_by=None,
            sort=None,
            search=None,
            group=InboxGroup.COMPLETED,
        )


@pytest.mark.anyio
async def test_unfiltered_list_omits_optional_predicates() -> None:
    """No filters -> no entity_type equality or date-range predicates emitted."""
    session = _RecordingSession()
    provider = AgentRunsInboxProvider(cast(AsyncSession, session), _role())

    await provider.list_items(limit=10)

    compiled = str(session.statements[-1].compile())
    # The base query always excludes the "approval" entity_type via `!=`, so an
    # equality predicate would only appear from the entity_type filter.
    assert "agent_session.entity_type =" not in compiled
    assert "agent_session.created_at >=" not in compiled
    assert "agent_session.updated_at >=" not in compiled
