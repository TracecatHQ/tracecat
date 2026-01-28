"""Approvals inbox provider for workflow-initiated agent sessions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, or_, select

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import AgentSession, Approval, Workflow
from tracecat.inbox.schemas import InboxItemRead, WorkflowSummary
from tracecat.inbox.types import InboxItemStatus, InboxItemType
from tracecat.logger import logger
from tracecat.pagination import BaseCursorPaginator, CursorPaginatedResponse

if TYPE_CHECKING:
    from tracecat.auth.types import Role


class ApprovalsInboxProvider(BaseCursorPaginator):
    """Provides approval items for the inbox.

    Filters to workflow-initiated sessions only and enriches with workflow metadata.
    """

    def __init__(self, session: AsyncDBSession, role: Role):
        super().__init__(session)
        self.role = role
        self.workspace_id = role.workspace_id

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InboxItemRead]:
        """List workflow approval items with simple pagination."""
        # Query workflow-initiated sessions with approvals
        stmt = (
            select(AgentSession)
            .join(Approval, AgentSession.id == Approval.session_id)
            .where(
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.parent_session_id.is_(None),  # Exclude forked sessions
                AgentSession.entity_type == "workflow",  # Workflow-only
            )
            .distinct()
            .order_by(AgentSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(stmt)
        sessions = result.scalars().all()

        return await self._enrich_sessions(sessions)

    async def list_items_paginated(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        reverse: bool = False,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List workflow approval items with cursor pagination."""
        # Base query for workflow-initiated sessions with approvals
        base_stmt = (
            select(AgentSession)
            .join(Approval, AgentSession.id == Approval.session_id)
            .where(
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.parent_session_id.is_(None),
                AgentSession.entity_type == "workflow",
            )
            .distinct()
        )

        # Determine sort column and direction
        sort_col = order_by or "created_at"
        sort_desc = sort != "asc"

        # Apply ordering
        if sort_col == "created_at":
            order_clause = (
                AgentSession.created_at.desc()
                if sort_desc
                else AgentSession.created_at.asc()
            )
        elif sort_col == "updated_at":
            order_clause = (
                AgentSession.updated_at.desc()
                if sort_desc
                else AgentSession.updated_at.asc()
            )
        else:
            order_clause = AgentSession.created_at.desc()

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
                logger.warning(f"Invalid cursor: {e}")

        # Apply ordering with id as secondary sort for stable pagination
        id_order = AgentSession.id.desc() if sort_desc else AgentSession.id.asc()
        stmt = base_stmt.order_by(order_clause, id_order).limit(limit + 1)

        result = await self.session.execute(stmt)
        sessions = list(result.scalars().all())

        # Check if there are more items
        has_more = len(sessions) > limit
        if has_more:
            sessions = sessions[:limit]

        # Reverse if needed
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

    async def _enrich_sessions(
        self,
        sessions: Sequence[AgentSession],
    ) -> list[InboxItemRead]:
        """Transform sessions to inbox items with workflow metadata."""
        if not sessions:
            return []

        session_ids = [s.id for s in sessions]

        # Fetch approvals for these sessions
        approval_stmt = select(Approval).where(
            Approval.workspace_id == self.workspace_id,
            Approval.session_id.in_(session_ids),
        )
        approval_result = await self.session.execute(approval_stmt)
        approvals = approval_result.scalars().all()

        # Group approvals by session
        approvals_by_session: dict[uuid.UUID, list[Approval]] = {}
        for approval in approvals:
            if approval.session_id:
                approvals_by_session.setdefault(approval.session_id, []).append(
                    approval
                )

        # Fetch workflow metadata for sessions with entity_id
        workflow_ids = {s.entity_id for s in sessions if s.entity_id}
        workflows_by_id: dict[uuid.UUID, Workflow] = {}

        if workflow_ids:
            workflow_stmt = select(Workflow).where(Workflow.id.in_(list(workflow_ids)))
            workflow_result = await self.session.execute(workflow_stmt)
            workflows = workflow_result.scalars().all()
            workflows_by_id = {w.id: w for w in workflows}

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

            if pending_count > 0:
                status = InboxItemStatus.PENDING
            elif failed_count > 0:
                status = InboxItemStatus.FAILED
            else:
                status = InboxItemStatus.COMPLETED

            # Build preview text
            if pending_count > 0:
                preview = f"{pending_count} pending approval{'s' if pending_count != 1 else ''}"
            elif failed_count > 0:
                preview = f"{failed_count} rejected"
            else:
                preview = "All approvals completed"

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
                    type=InboxItemType.APPROVAL,
                    title=title,
                    preview=preview,
                    status=status,
                    unread=pending_count > 0,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    workflow=workflow_summary,
                    source_id=session.id,  # Always use parent session ID
                    source_type="agent_session",
                    metadata=metadata,
                )
            )

        return items
