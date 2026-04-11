"""Approvals provider for workflow-initiated agent sessions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, or_, select
from temporalio.client import WorkflowExecutionStatus

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.approvals.schemas import ApprovalItemRead, WorkflowSummary
from tracecat.approvals.types import ApprovalItemStatus, ApprovalItemType
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import AgentSession, Approval, Workflow
from tracecat.dsl.client import get_temporal_client
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

if TYPE_CHECKING:
    from tracecat.auth.types import Role


class ApprovalsProvider(BaseCursorPaginator):
    """Provides approval items for the approvals queue.

    Filters to workflow-initiated sessions only and enriches with workflow metadata.
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
                    "Failed to describe agent workflow for approval status",
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

    async def list_approvals(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        reverse: bool = False,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[ApprovalItemRead]:
        """List workflow approval items with cursor pagination."""
        # Base query for workflow-initiated sessions with approvals
        base_stmt = (
            select(AgentSession)
            .join(Approval, AgentSession.id == Approval.session_id)
            .where(
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.parent_session_id.is_(None),
                AgentSession.entity_type.in_(["workflow", "external_channel"]),
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
    ) -> list[ApprovalItemRead]:
        """Transform sessions to approval items with workflow metadata."""
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
        temporal_statuses = await self._resolve_temporal_statuses(sessions)

        if workflow_ids:
            workflow_stmt = select(Workflow).where(Workflow.id.in_(list(workflow_ids)))
            workflow_result = await self.session.execute(workflow_stmt)
            workflows = workflow_result.scalars().all()
            workflows_by_id = {w.id: w for w in workflows}

        # Transform to ApprovalItemRead
        items: list[ApprovalItemRead] = []
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
                status = ApprovalItemStatus.PENDING
            elif temporal_status in FAILED_STATUSES:
                status = ApprovalItemStatus.FAILED
            elif failed_count > 0:
                status = ApprovalItemStatus.FAILED
            else:
                status = ApprovalItemStatus.COMPLETED

            # Build preview text
            if pending_count > 0:
                preview = f"{pending_count} pending approval{'s' if pending_count != 1 else ''}"
            elif temporal_status in RUNNING_STATUSES:
                preview = "Execution in progress"
            elif temporal_status in FAILED_STATUSES:
                preview = "Execution failed"
            elif failed_count > 0:
                preview = f"{failed_count} rejected"
            else:
                preview = "Execution completed"

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
                # Use workflow alias or title as the approval item title
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
                ApprovalItemRead(
                    id=session.id,
                    type=ApprovalItemType.APPROVAL,
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
