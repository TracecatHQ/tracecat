"""Agent runs inbox provider for Claude Code agent sessions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import String, and_, cast, distinct, func, or_, select
from sqlalchemy.orm import load_only
from sqlalchemy.sql import Select
from temporalio.client import WorkflowExecutionStatus

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import AgentSession, Approval, User, Workflow
from tracecat.dsl.client import get_temporal_client
from tracecat.inbox.schemas import InboxItemRead, UserSummary, WorkflowSummary
from tracecat.inbox.types import InboxGroup, InboxItemStatus, InboxItemType
from tracecat.logger import logger
from tracecat.pagination import BaseCursorPaginator, CursorPaginatedResponse
from tracecat_ee.agent.types import AgentWorkflowID

RUNNING_STATUSES = {
    WorkflowExecutionStatus.RUNNING,
    WorkflowExecutionStatus.CONTINUED_AS_NEW,
}
FAILED_STATUSES = {
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.TIMED_OUT,
    WorkflowExecutionStatus.TERMINATED,
}

# Harnesses whose root sessions surface in the inbox. These are the durable
# agent harnesses; chat-only harnesses are handled inline in the chat UI and do
# not appear here. Add a harness to this set to make its runs inbox-eligible.
INBOX_HARNESS_TYPES = (HarnessType.CLAUDE_CODE,)

# Group membership depends on live Temporal status, so grouped listing scans
# sessions in batches and classifies after enrichment. Each scanned session may
# cost a Temporal describe call, so the scan is hard-capped per request.
GROUP_SCAN_BATCH_SIZE = 50
GROUP_SCAN_MAX_SESSIONS = 300

RUNNING_STATUS_NAMES = {s.name for s in RUNNING_STATUSES}
FAILED_STATUS_NAMES = {s.name for s in FAILED_STATUSES}

if TYPE_CHECKING:
    from tracecat.auth.types import Role


class AgentRunsInboxProvider(BaseCursorPaginator):
    """Provides agent run items for the inbox.

    Lists root sessions for inbox-eligible harnesses (see
    ``INBOX_HARNESS_TYPES``), plus any legacy sessions with approvals, and
    enriches them with approval and workflow metadata.
    """

    def __init__(self, session: AsyncDBSession, role: Role):
        super().__init__(session)
        self.role = role
        self.workspace_id = role.workspace_id

    async def _resolve_temporal_statuses(
        self,
        sessions: Sequence[AgentSession],
    ) -> dict[uuid.UUID, WorkflowExecutionStatus]:
        if not sessions:
            return {}

        session_pairs = [
            (session.id, session.curr_run_id)
            for session in sessions
            if session.curr_run_id is not None
        ]
        if not session_pairs:
            return {}

        from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

        client = await get_temporal_client()

        async def describe_status(
            session_id: uuid.UUID, run_id: uuid.UUID
        ) -> tuple[uuid.UUID, WorkflowExecutionStatus | None]:
            try:
                workflow_id = AgentWorkflowID(run_id)
                handle = client.get_workflow_handle_for(
                    DurableAgentWorkflow.run,
                    str(workflow_id),
                )
                description = await handle.describe()
                return session_id, description.status
            except Exception as exc:
                logger.warning(
                    "Failed to describe agent workflow for inbox status",
                    session_id=str(session_id),
                    run_id=str(run_id),
                    error=str(exc),
                )
                return session_id, None

        results = await asyncio.gather(
            *(
                describe_status(session_id, run_id)
                for session_id, run_id in session_pairs
            )
        )
        statuses: dict[uuid.UUID, WorkflowExecutionStatus] = {}
        for session_id, status in results:
            if status is not None:
                statuses[session_id] = status
        return statuses

    def _base_query(
        self,
        search: str | None,
        *,
        entity_type: AgentSessionEntity | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
    ) -> Select[tuple[AgentSession]]:
        """Base statement selecting inbox-eligible root sessions.

        ``entity_type`` and the date filters are applied here so they narrow the
        keyset selection in SQL. Filtering client-side instead would let the
        server fill a page with rows the client discards, making groups look
        short/empty and tying ``has_more`` to the unfiltered page.
        """
        # Root sessions only: runs of any inbox-eligible harness, plus legacy
        # sessions that already have approvals so existing inbox items don't
        # disappear.
        has_approvals = (
            select(Approval.id).where(Approval.session_id == AgentSession.id).exists()
        )
        base_stmt = select(AgentSession).where(
            AgentSession.workspace_id == self.workspace_id,
            AgentSession.parent_session_id.is_(None),
            AgentSession.entity_type != "approval",
            or_(
                AgentSession.harness_type.in_(INBOX_HARNESS_TYPES),
                has_approvals,
            ),
        )

        if entity_type is not None:
            base_stmt = base_stmt.where(AgentSession.entity_type == entity_type)
        if created_after is not None:
            base_stmt = base_stmt.where(AgentSession.created_at >= created_after)
        if updated_after is not None:
            base_stmt = base_stmt.where(AgentSession.updated_at >= updated_after)

        # The inbox polls multiple groups and the grouped scan can hydrate up to
        # GROUP_SCAN_MAX_SESSIONS sessions per request. Load only the columns the
        # provider actually reads so we don't repeatedly pull heavy JSONB fields
        # (tools, mcp_integrations, agents_binding, work_dir_snapshot, artifacts)
        # that enrichment never touches. `id` is loaded explicitly because the
        # PK is `surrogate_id`, so SQLAlchemy would otherwise skip the UUID `id`.
        base_stmt = base_stmt.options(
            load_only(
                AgentSession.id,
                AgentSession.title,
                AgentSession.created_by,
                AgentSession.entity_type,
                AgentSession.entity_id,
                AgentSession.curr_run_id,
                AgentSession.created_at,
                AgentSession.updated_at,
                raiseload=True,
            )
        )

        if search:
            like_term = f"%{search}%"
            # Workflow-initiated sessions display the workflow alias/title in
            # the inbox, so match those as well as the session title.
            workflow_match = (
                select(Workflow.id)
                .where(
                    Workflow.workspace_id == self.workspace_id,
                    Workflow.id == AgentSession.entity_id,
                    or_(
                        Workflow.title.ilike(like_term),
                        Workflow.alias.ilike(like_term),
                    ),
                )
                .exists()
            )
            base_stmt = base_stmt.where(
                or_(
                    AgentSession.title.ilike(like_term),
                    cast(AgentSession.entity_id, String).ilike(like_term),
                    workflow_match,
                )
            )
        return base_stmt

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
        entity_type: AgentSessionEntity | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List agent run items with cursor pagination."""
        if group is not None:
            return await self._list_items_grouped(
                limit=limit,
                cursor=cursor,
                reverse=reverse,
                order_by=order_by,
                sort=sort,
                search=search,
                group=group,
                entity_type=entity_type,
                created_after=created_after,
                updated_after=updated_after,
            )

        base_stmt = self._base_query(
            search,
            entity_type=entity_type,
            created_after=created_after,
            updated_after=updated_after,
        )

        # Determine sort column and direction
        sort_col = order_by or "created_at"
        sort_desc = sort != "asc"
        # Scan in the direction that walks toward the rows adjacent to the
        # cursor. Reverse pagination flips the scan so the LIMIT keeps the rows
        # closest to the cursor; the page is reversed back into display order
        # below. Without this flip a reverse page orders descending before
        # truncating and skips the rows immediately before the cursor.
        scan_desc = sort_desc if not reverse else not sort_desc

        sort_column = (
            AgentSession.updated_at
            if sort_col == "updated_at"
            else AgentSession.created_at
        )
        order_clause = sort_column.desc() if scan_desc else sort_column.asc()

        # Apply cursor filtering with composite (sort_value, id) for stable pagination
        if cursor:
            try:
                cursor_data = self.decode_cursor(cursor)
                cursor_value = cursor_data.sort_value
                cursor_id = uuid.UUID(cursor_data.id)

                # Select the correct column based on sort_col
                cursor_col = (
                    AgentSession.updated_at
                    if sort_col == "updated_at"
                    else AgentSession.created_at
                )

                # Use composite filter: (sort_col, id) to handle timestamp collisions
                if sort_desc:
                    if reverse:
                        # Going backwards from cursor in desc order = get items after cursor
                        base_stmt = base_stmt.where(
                            or_(
                                cursor_col > cursor_value,
                                and_(
                                    cursor_col == cursor_value,
                                    AgentSession.id > cursor_id,
                                ),
                            )
                        )
                    else:
                        # Going forward from cursor in desc order = get items before cursor
                        base_stmt = base_stmt.where(
                            or_(
                                cursor_col < cursor_value,
                                and_(
                                    cursor_col == cursor_value,
                                    AgentSession.id < cursor_id,
                                ),
                            )
                        )
                else:
                    if reverse:
                        base_stmt = base_stmt.where(
                            or_(
                                cursor_col < cursor_value,
                                and_(
                                    cursor_col == cursor_value,
                                    AgentSession.id < cursor_id,
                                ),
                            )
                        )
                    else:
                        base_stmt = base_stmt.where(
                            or_(
                                cursor_col > cursor_value,
                                and_(
                                    cursor_col == cursor_value,
                                    AgentSession.id > cursor_id,
                                ),
                            )
                        )
            except (ValueError, KeyError) as e:
                # Surface malformed cursors as a client error so the router can
                # return 400 instead of silently falling back to the first page.
                raise ValueError(f"Invalid cursor: {e}") from e

        # Apply ordering with id as secondary sort for stable pagination. The id
        # tiebreaker must match the scan direction so the composite keyset stays
        # consistent with the cursor predicates above.
        id_order = AgentSession.id.desc() if scan_desc else AgentSession.id.asc()
        stmt = base_stmt.order_by(order_clause, id_order).limit(limit + 1)

        result = await self.session.execute(stmt)
        sessions = list(result.scalars().all())

        # Check if there are more items
        has_more = len(sessions) > limit
        if has_more:
            sessions = sessions[:limit]

        # The scan ran in scan_desc order; reverse back into display order so
        # the page reads in the requested sort regardless of pagination
        # direction.
        if reverse:
            sessions.reverse()

        # Enrich sessions
        items = await self._enrich_sessions(sessions)

        # Generate cursors
        next_cursor = None
        prev_cursor = None

        if items:
            if has_more:
                last_item = items[-1]
                # Get the session for this item to access the sort column value
                last_session = next(
                    (s for s in sessions if s.id == last_item.source_id), None
                )
                if last_session:
                    # Use the correct column value based on sort_col
                    sort_value = (
                        last_session.updated_at
                        if sort_col == "updated_at"
                        else last_session.created_at
                    )
                    next_cursor = self.encode_cursor(
                        id=last_item.id,
                        sort_column=sort_col,
                        sort_value=sort_value,
                    )
            if cursor:
                first_item = items[0]
                first_session = next(
                    (s for s in sessions if s.id == first_item.source_id), None
                )
                if first_session:
                    # Use the correct column value based on sort_col
                    sort_value = (
                        first_session.updated_at
                        if sort_col == "updated_at"
                        else first_session.created_at
                    )
                    prev_cursor = self.encode_cursor(
                        id=first_item.id,
                        sort_column=sort_col,
                        sort_value=sort_value,
                    )

        return CursorPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=cursor is not None,
            total_estimate=None,
        )

    async def _fetch_approval_counts(
        self,
        session_ids: Sequence[uuid.UUID],
        status: ApprovalStatus,
    ) -> dict[uuid.UUID, int]:
        """Return the count of approvals with ``status`` per session id.

        Sessions with no matching approvals are absent from the mapping.
        """
        if not session_ids:
            return {}

        stmt = (
            select(Approval.session_id, func.count(Approval.id))
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id.in_(list(session_ids)),
                Approval.status == status,
            )
            .group_by(Approval.session_id)
        )
        result = await self.session.execute(stmt)
        return {
            session_id: int(count or 0)
            for session_id, count in result.all()
            if session_id is not None
        }

    async def _classify_sessions_for_group(
        self,
        sessions: Sequence[AgentSession],
        *,
        group: InboxGroup,
    ) -> dict[uuid.UUID, InboxGroup]:
        """Classify sessions into display groups without full enrichment.

        Pulls only the signals group membership needs so scanned-but-discarded
        sessions skip workflow/user lookups, metadata construction, and
        serialization. The REVIEW_REQUIRED path additionally skips the Temporal
        status fan-out, which it does not depend on.

        The group rules below must stay in sync with the status grouping in the
        inbox UI.
        """
        session_ids = [s.id for s in sessions]
        pending_counts = await self._fetch_approval_counts(
            session_ids, ApprovalStatus.PENDING
        )
        rejected_counts = await self._fetch_approval_counts(
            session_ids, ApprovalStatus.REJECTED
        )

        if group is InboxGroup.REVIEW_REQUIRED:
            return {
                s.id: (
                    InboxGroup.REVIEW_REQUIRED
                    if pending_counts.get(s.id, 0) > 0
                    else InboxGroup.COMPLETED
                )
                for s in sessions
            }

        temporal_statuses = await self._resolve_temporal_statuses(sessions)

        classifications: dict[uuid.UUID, InboxGroup] = {}
        for s in sessions:
            if pending_counts.get(s.id, 0) > 0:
                classifications[s.id] = InboxGroup.REVIEW_REQUIRED
                continue
            temporal_status = temporal_statuses.get(s.id)
            if temporal_status in RUNNING_STATUSES:
                classifications[s.id] = InboxGroup.RUNNING
            elif temporal_status in FAILED_STATUSES:
                classifications[s.id] = InboxGroup.ERROR
            elif rejected_counts.get(s.id, 0) > 0:
                classifications[s.id] = InboxGroup.ERROR
            else:
                classifications[s.id] = InboxGroup.COMPLETED
        return classifications

    async def _list_items_grouped(
        self,
        *,
        limit: int,
        cursor: str | None,
        reverse: bool,
        order_by: str | None,
        sort: Literal["asc", "desc"] | None,
        search: str | None,
        group: InboxGroup,
        entity_type: AgentSessionEntity | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List items belonging to a single display group.

        Group membership requires enrichment (approval counts and live Temporal
        status), so this scans sessions in keyset batches, classifies each
        batch, and stops once enough matches are collected or the scan cap is
        reached.

        The cursor encodes the scan position (created_at/updated_at + id of the
        last scanned session), not the last returned item. This means "show
        more" resumes the scan where it left off rather than restarting from the
        top, so groups whose matching items sit beyond GROUP_SCAN_MAX_SESSIONS
        candidates are still reachable.
        """
        sort_col = "updated_at" if order_by == "updated_at" else "created_at"
        sort_desc = sort != "asc"
        scan_desc = sort_desc if not reverse else not sort_desc
        column = (
            AgentSession.updated_at
            if sort_col == "updated_at"
            else AgentSession.created_at
        )

        base_stmt = self._base_query(
            search,
            entity_type=entity_type,
            created_after=created_after,
            updated_after=updated_after,
        )
        # Narrow the scan with SQL predicates where group membership implies one
        if group is InboxGroup.REVIEW_REQUIRED:
            pending_exists = (
                select(Approval.id)
                .where(
                    Approval.session_id == AgentSession.id,
                    Approval.status == ApprovalStatus.PENDING,
                )
                .exists()
            )
            base_stmt = base_stmt.where(
                pending_exists,
            )
        elif group is InboxGroup.RUNNING:
            # Necessary (not sufficient) condition: a live Temporal run exists
            base_stmt = base_stmt.where(AgentSession.curr_run_id.is_not(None))

        # Decode cursor as a scan-position keyset (sort_value + id of last
        # scanned session). This lets subsequent pages resume exactly where
        # the previous scan stopped instead of restarting from the top.
        last_key: tuple[Any, uuid.UUID] | None = None
        if cursor:
            try:
                cursor_data = self.decode_cursor(cursor)
                last_key = (cursor_data.sort_value, uuid.UUID(cursor_data.id))
            except (ValueError, KeyError) as e:
                # Surface malformed cursors as a client error so the router can
                # return 400 instead of silently restarting the scan.
                raise ValueError(f"Invalid grouped inbox cursor: {e}") from e

        matches: list[AgentSession] = []
        scanned = 0
        exhausted = False

        while len(matches) < limit + 1 and scanned < GROUP_SCAN_MAX_SESSIONS:
            stmt = base_stmt
            if last_key is not None:
                last_value, last_id = last_key
                if scan_desc:
                    stmt = stmt.where(
                        or_(
                            column < last_value,
                            and_(column == last_value, AgentSession.id < last_id),
                        )
                    )
                else:
                    stmt = stmt.where(
                        or_(
                            column > last_value,
                            and_(column == last_value, AgentSession.id > last_id),
                        )
                    )
            order_clause = column.desc() if scan_desc else column.asc()
            id_order = AgentSession.id.desc() if scan_desc else AgentSession.id.asc()
            stmt = stmt.order_by(order_clause, id_order).limit(GROUP_SCAN_BATCH_SIZE)

            result = await self.session.execute(stmt)
            sessions = list(result.scalars().all())
            if not sessions:
                exhausted = True
                break

            scanned += len(sessions)
            last_session = sessions[-1]
            last_key = (getattr(last_session, sort_col), last_session.id)

            # Lightweight pass: classify the batch from raw signals only, so
            # discarded sessions never pay for full enrichment.
            classifications = await self._classify_sessions_for_group(
                sessions, group=group
            )
            matches.extend(
                session for session in sessions if classifications[session.id] == group
            )

            if len(sessions) < GROUP_SCAN_BATCH_SIZE:
                exhausted = True
                break

        # Full enrichment runs only for the sessions that make the page.
        page_sessions = matches[:limit]
        raw_has_more = len(matches) > limit or not exhausted
        next_cursor: str | None = None
        if raw_has_more:
            if last_key is not None:
                if len(matches) > limit:
                    # Item-based cursor: skip past the last item on this page.
                    last_session = page_sessions[-1]
                    next_cursor = self.encode_cursor(
                        id=last_session.id,
                        sort_column=sort_col,
                        sort_value=getattr(last_session, sort_col),
                    )
                else:
                    # Scan-position cursor: resume scanning from where we stopped.
                    scan_value, scan_id = last_key
                    next_cursor = self.encode_cursor(
                        id=scan_id,
                        sort_column=sort_col,
                        sort_value=scan_value,
                    )

        prev_cursor: str | None = None
        if cursor and page_sessions:
            first_session = page_sessions[0]
            prev_cursor = self.encode_cursor(
                id=first_session.id,
                sort_column=sort_col,
                sort_value=getattr(first_session, sort_col),
            )

        if reverse:
            page_sessions.reverse()

        page_items = await self._enrich_sessions(page_sessions)
        if reverse:
            next_cursor, prev_cursor = prev_cursor, next_cursor
            # In reverse mode "next" walks back toward newer items, which only
            # exists when this page produced an anchor cursor. Tying it to a bare
            # `cursor is not None` would advertise has_more=true with
            # next_cursor=None on an empty page, enabling a dead pagination
            # control.
            has_more = next_cursor is not None
            has_previous = raw_has_more
        else:
            has_more = raw_has_more
            has_previous = cursor is not None

        return CursorPaginatedResponse(
            items=page_items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=None,
        )

    async def count_pending_items(self) -> int:
        """Count pending approval inbox items for sessions shown in the inbox."""
        stmt = (
            select(func.count(distinct(AgentSession.id)))
            .select_from(AgentSession)
            .join(Approval, Approval.session_id == AgentSession.id)
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.status == ApprovalStatus.PENDING,
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.parent_session_id.is_(None),
            )
        )
        count = await self.session.scalar(stmt)
        return int(count or 0)

    async def _enrich_sessions(
        self,
        sessions: Sequence[AgentSession],
    ) -> list[InboxItemRead]:
        """Transform sessions to inbox items with workflow metadata."""
        if not sessions:
            return []

        approval_session_ids = [s.id for s in sessions]
        approvals_by_session: dict[uuid.UUID, list[Approval]] = {}
        if approval_session_ids:
            approval_stmt = select(Approval).where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id.in_(approval_session_ids),
            )
            approval_result = await self.session.execute(approval_stmt)
            for approval in approval_result.scalars().all():
                if approval.session_id:
                    approvals_by_session.setdefault(approval.session_id, []).append(
                        approval
                    )

        # Fetch workflow metadata for sessions with entity_id
        workflow_ids = {s.entity_id for s in sessions if s.entity_id}
        workflows_by_id: dict[uuid.UUID, Workflow] = {}
        temporal_statuses = await self._resolve_temporal_statuses(sessions)

        if workflow_ids:
            workflow_stmt = select(Workflow).where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id.in_(list(workflow_ids)),
            )
            workflow_result = await self.session.execute(workflow_stmt)
            workflows = workflow_result.scalars().all()
            workflows_by_id = {w.id: w for w in workflows}

        # Fetch creators for user-initiated sessions
        creator_ids = {s.created_by for s in sessions if s.created_by}
        users_by_id: dict[uuid.UUID, User] = {}
        if creator_ids:
            user_stmt = select(User).where(
                User.id.in_(list(creator_ids))  # pyright: ignore[reportAttributeAccessIssue]
            )
            user_result = await self.session.execute(user_stmt)
            users_by_id = {u.id: u for u in user_result.scalars().all()}

        # Transform to InboxItemRead
        items: list[InboxItemRead] = []
        for session in sessions:
            session_approvals = approvals_by_session.get(session.id, [])

            # Calculate status
            pending_count = sum(
                1 for a in session_approvals if a.status == ApprovalStatus.PENDING
            )
            failed_count = sum(
                1 for a in session_approvals if a.status == ApprovalStatus.REJECTED
            )
            temporal_status = temporal_statuses.get(session.id)
            temporal_status_name = temporal_status.name if temporal_status else None

            if pending_count > 0:
                status = InboxItemStatus.PENDING
            elif temporal_status in FAILED_STATUSES:
                status = InboxItemStatus.FAILED
            elif failed_count > 0:
                status = InboxItemStatus.FAILED
            else:
                status = InboxItemStatus.COMPLETED

            # Build preview text
            if pending_count > 0:
                preview = f"{pending_count} pending approval{'s' if pending_count != 1 else ''}"
            elif temporal_status in RUNNING_STATUSES:
                preview = "Execution in progress"
            elif temporal_status in FAILED_STATUSES:
                preview = "Execution failed"
            elif failed_count > 0:
                preview = f"{failed_count} rejected"
            elif temporal_status is None and not session_approvals:
                preview = "Agent session"
            else:
                preview = "Execution completed"

            # Get creator info
            created_by: UserSummary | None = None
            if session.created_by and session.created_by in users_by_id:
                user = users_by_id[session.created_by]
                created_by = UserSummary(
                    id=user.id,
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                )

            # Get workflow info
            workflow_summary: WorkflowSummary | None = None
            title = session.title or "Agent session"

            if session.entity_id and session.entity_id in workflows_by_id:
                workflow = workflows_by_id[session.entity_id]
                workflow_summary = WorkflowSummary(
                    id=workflow.id,
                    title=workflow.title or "Untitled workflow",
                    alias=workflow.alias,
                )
                # Use workflow alias or title as the inbox item title
                title = workflow.alias or workflow.title or title

            # Build metadata
            metadata: dict[str, Any] = {
                "entity_type": session.entity_type,
                "entity_id": str(session.entity_id) if session.entity_id else None,
                "pending_count": pending_count,
                "total_approvals": len(session_approvals),
                "temporal_status": temporal_status_name,
                "approvals": [
                    {
                        "id": str(a.id),
                        "tool_name": a.tool_name,
                        "status": a.status,
                    }
                    for a in session_approvals
                ],
            }

            items.append(
                InboxItemRead(
                    id=session.id,
                    type=(
                        InboxItemType.APPROVAL
                        if session_approvals
                        else InboxItemType.AGENT_RUN
                    ),
                    title=title,
                    preview=preview,
                    status=status,
                    unread=pending_count > 0,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    workflow=workflow_summary,
                    created_by=created_by,
                    source_id=session.id,  # Always use parent session ID
                    source_type="agent_session",
                    metadata=metadata,
                )
            )

        return items
