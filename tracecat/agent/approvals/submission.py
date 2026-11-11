"""Record approval decisions on their rows, then submit them to the waiting run."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import exists, func, null, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from tracecat.agent.approvals.decisions import (
    SubmittedDecision,
    decision_matches_row,
    record_decision,
    recorded_metadata,
    recorded_result,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.registry import get_agent_backend
from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import AgentControlRejected, AgentControlUncertain
from tracecat.agent.channel_publisher import approval_batch_id
from tracecat.agent.common.stream_types import (
    ApprovalStreamStatus,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.feed import SessionFeedEventType, publish_session_event
from tracecat.db.models import AgentSession, Approval
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService

STALLED_SUBMISSION_SECONDS = 10.0
"""How long a continuation may hold only its marker before it is resubmitted.

A healthy submission writes after the marker within a second; resubmitting
sooner is safe, only wasteful."""


class ApprovalNoLongerActiveError(TracecatConflictError):
    """The tool call was decided differently, or its run no longer waits on it."""


class ApprovalDecisionInvalidError(ValueError):
    """The decisions do not name tool calls of one approval batch in the session."""


@dataclass(frozen=True, slots=True)
class _LockedBatch:
    """The session and approval batch a submission holds row locks on."""

    agent_session: AgentSession
    run_id: uuid.UUID
    backend: AgentBackend[Any, Any]
    rows: list[Approval]


@dataclass(frozen=True, slots=True)
class _RecordedDecisionSet:
    """A committed decision set, ready to submit to the waiting run."""

    run_id: uuid.UUID
    backend: AgentBackend[Any, Any]
    # A repeat of a set the run already moved past: submitting it is a no-op.
    outdated: bool
    stream_id: uuid.UUID
    previous_stream_id: uuid.UUID | None
    newly_recorded_tool_call_ids: list[str]
    batch_tool_call_ids: list[str]
    pending_tool_call_ids: list[str]
    submission: WorkflowApprovalSubmission


@dataclass(frozen=True, slots=True)
class ApprovalSubmissionResult:
    """What one submission recorded and submitted to the waiting run."""

    run_id: uuid.UUID
    # The stream carrying the run's output for this caller to follow.
    stream_id: uuid.UUID
    pending_tool_call_ids: list[str]
    # ``noop``: the set was outdated and not submitted; ``submitted``: the run
    # took the set but this call didn't resume it; ``resumed``: this call
    # resumed the run and owns announcing it.
    outcome: Literal["noop", "submitted", "resumed"]


def _approval_continuation_stream_id(
    run_id: uuid.UUID, tool_call_ids: Sequence[str]
) -> uuid.UUID:
    """Name the continuation that submits a run's recorded decisions.

    Every submitter of the same recorded set derives the same stream, and so
    the same Temporal update id: a retry or replay is applied at most once.
    """
    return uuid.uuid5(run_id, json.dumps(sorted(tool_call_ids)))


class ApprovalSubmissionService(BaseWorkspaceService):
    """Records approval decisions and submits them to the run waiting on them."""

    service_name = "approval-submission"

    async def _lock_approval_batch(
        self, session_id: uuid.UUID, tool_call_ids: Sequence[str]
    ) -> list[Approval]:
        """Lock every row of the approval batches that hold ``tool_call_ids``.

        A batch is the rows written in one transaction, so they share
        ``created_at``. Rows recorded before batches were written together
        each form a batch of their own.

        Raises:
            ApprovalDecisionInvalidError: If an ID has no row in the session.
        """
        in_session = (
            Approval.workspace_id == self.workspace_id,
            Approval.session_id == session_id,
        )
        batches = select(Approval.created_at).where(
            *in_session, Approval.tool_call_id.in_(tool_call_ids)
        )
        rows = list(
            (
                await self.session.scalars(
                    select(Approval)
                    .where(*in_session, Approval.created_at.in_(batches))
                    .order_by(Approval.tool_call_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        unexpected = sorted(set(tool_call_ids) - {row.tool_call_id for row in rows})
        if not tool_call_ids or unexpected:
            raise ApprovalDecisionInvalidError(
                "Approval decisions do not match pending tool calls"
                f" (unexpected={unexpected})"
            )
        return rows

    async def _lock_decided_batch(
        self,
        *,
        session_id: uuid.UUID,
        expected_batch_id: uuid.UUID | None,
        decisions: Mapping[str, SubmittedDecision],
    ) -> _LockedBatch:
        """Lock the session and the batch, then check the decisions against them."""
        agent_session = await self.session.scalar(
            select(AgentSession)
            .where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
            )
            # NO KEY UPDATE: serializes submitters without blocking runtime FK checks.
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if agent_session is None:
            raise TracecatNotFoundError(
                f"No active agent session found with ID {session_id}"
            )
        run_id = agent_session.curr_run_id
        if run_id is None:
            raise TracecatNotFoundError(
                f"No active workflow run for session {session_id}"
            )
        backend = get_agent_backend(
            agent_session.backend_id, harness_type=agent_session.harness_type
        )

        rows = await self._lock_approval_batch(session_id, list(decisions))
        if expected_batch_id is not None and expected_batch_id != approval_batch_id(
            run_id, (row.tool_call_id for row in rows)
        ):
            raise ApprovalNoLongerActiveError(
                f"Approval batch {expected_batch_id} is no longer waiting on decisions"
            )
        by_id = {row.tool_call_id: row for row in rows}
        for tool_call_id, submitted in decisions.items():
            row = by_id[tool_call_id]
            if row.status is not ApprovalStatus.PENDING and (
                not decision_matches_row(submitted.result, row)
            ):
                raise ApprovalNoLongerActiveError(
                    "Approval decision conflicts with a decision already"
                    f" recorded for tool call {tool_call_id}"
                )
        return _LockedBatch(
            agent_session=agent_session, run_id=run_id, backend=backend, rows=rows
        )

    async def _record_decisions(
        self,
        *,
        session_id: uuid.UUID,
        expected_batch_id: uuid.UUID | None,
        decisions: Mapping[str, SubmittedDecision],
        approved_by: uuid.UUID | None,
    ) -> _RecordedDecisionSet:
        """Record pending decisions and install their continuation, in one commit."""
        try:
            batch = await self._lock_decided_batch(
                session_id=session_id,
                expected_batch_id=expected_batch_id,
                decisions=decisions,
            )
            by_id = {row.tool_call_id: row for row in batch.rows}
            newly_recorded = [
                tool_call_id
                for tool_call_id in decisions
                if by_id[tool_call_id].status is ApprovalStatus.PENDING
            ]
            for tool_call_id in newly_recorded:
                record_decision(
                    by_id[tool_call_id],
                    decisions[tool_call_id],
                    approved_by=approved_by,
                )
            decided = [
                row for row in batch.rows if row.status is not ApprovalStatus.PENDING
            ]
            stream_id = _approval_continuation_stream_id(
                batch.run_id, [row.tool_call_id for row in decided]
            )
            previous_stream_id = batch.agent_session.active_stream_id
            outdated = False
            if newly_recorded:
                await self._install_approval_continuation(
                    batch.agent_session, stream_id=stream_id
                )
            elif previous_stream_id != stream_id or await self._has_newer_batch(
                session_id, [by_id[tool_call_id] for tool_call_id in decisions]
            ):
                # A later decision or batch already moved the run past this set.
                outdated = True
            await self.session.commit()
        except (
            TracecatNotFoundError,
            TracecatValidationError,
            ApprovalNoLongerActiveError,
            ApprovalDecisionInvalidError,
        ):
            # Raised before any write: commit releases the locks without
            # expiring the caller's loaded rows.
            await self.session.commit()
            raise
        except BaseException:
            await self.session.rollback()
            raise

        return _RecordedDecisionSet(
            run_id=batch.run_id,
            backend=batch.backend,
            outdated=outdated,
            stream_id=stream_id,
            previous_stream_id=previous_stream_id,
            newly_recorded_tool_call_ids=newly_recorded,
            batch_tool_call_ids=[row.tool_call_id for row in batch.rows],
            pending_tool_call_ids=[
                row.tool_call_id
                for row in batch.rows
                if row.status is ApprovalStatus.PENDING
            ],
            submission=WorkflowApprovalSubmission(
                approvals={row.tool_call_id: recorded_result(row) for row in decided},
                approved_by=approved_by,
                decision_metadata={
                    row.tool_call_id: metadata
                    for row in decided
                    if (metadata := recorded_metadata(row)) is not None
                }
                or None,
                new_stream_id=stream_id,
            ),
        )

    async def _has_newer_batch(
        self, session_id: uuid.UUID, decided: Sequence[Approval]
    ) -> bool:
        """Whether the run paused on another batch after ``decided`` were decided.

        Compares against decision time, not creation, so legacy siblings of one
        pause (committed row by row) never count as a newer batch.
        """
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        Approval.workspace_id == self.workspace_id,
                        Approval.session_id == session_id,
                        Approval.created_at > max(row.updated_at for row in decided),
                    )
                )
            )
        )

    async def record_and_submit(
        self,
        *,
        session_id: uuid.UUID,
        expected_batch_id: uuid.UUID | None,
        decisions: Mapping[str, SubmittedDecision],
        approved_by: uuid.UUID | None,
    ) -> ApprovalSubmissionResult:
        """Record decisions on their approval rows, then submit them to the run.

        The rows are the record: a decision commits before the run sees it.
        A submission carries every recorded decision of the batch under an
        update id derived from that set, so Temporal applies it once and a
        matching replay retries a lost submission. Locking the session row orders
        concurrent submitters, so one winner holds per tool call.

        Raises:
            TracecatNotFoundError: If the session or its run is gone.
            ApprovalNoLongerActiveError: If ``expected_batch_id`` is not the
                current run's batch, or a decision contradicts a recorded one.
            ApprovalDecisionInvalidError: If an ID is not an approval of the
                session.
            AgentControlRejected: If the run refused the decisions. This call's
                decisions are reverted.
            AgentControlUncertain: If the outcome is unknown, or a refused set
                could not be reverted; retry the same decisions.
        """
        decision_set = await self._record_decisions(
            session_id=session_id,
            expected_batch_id=expected_batch_id,
            decisions=decisions,
            approved_by=approved_by,
        )
        if decision_set.outdated:
            return ApprovalSubmissionResult(
                run_id=decision_set.run_id,
                stream_id=decision_set.stream_id,
                pending_tool_call_ids=decision_set.pending_tool_call_ids,
                outcome="noop",
            )

        logger.info(
            "Submitting recorded approval decisions",
            session_id=str(session_id),
            run_id=str(decision_set.run_id),
            newly_recorded=decision_set.newly_recorded_tool_call_ids,
            submitted=sorted(decision_set.submission.approvals),
        )
        try:
            resumed = await decision_set.backend.submit_approvals(
                decision_set.run_id, decision_set.submission
            )
        except AgentControlRejected:
            if decision_set.newly_recorded_tool_call_ids:
                await self._revert_rejected_decisions(
                    session_id=session_id,
                    run_id=decision_set.run_id,
                    stream_id=decision_set.stream_id,
                    previous_stream_id=decision_set.previous_stream_id,
                    tool_call_ids=decision_set.newly_recorded_tool_call_ids,
                )
            raise

        return await self._announce_submitted_decisions(
            session_id=session_id,
            decision_set=decision_set,
            resumed=resumed,
            tool_call_ids=list(decisions),
        )

    async def resubmit_stalled(
        self,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        stream_id: uuid.UUID,
    ) -> bool:
        """Submit the recorded set of a stalled continuation again.

        A crash between the commit and the submission leaves the session on a
        continuation that holds only its marker; the caller found it so. The
        resubmission reuses the set's update id, so one still in flight
        absorbs it.

        Returns whether the set was resubmitted.
        """
        in_session = (
            Approval.workspace_id == self.workspace_id,
            Approval.session_id == session_id,
        )
        latest = select(func.max(Approval.created_at)).where(*in_session)
        decided = (
            await self.session.scalars(
                select(Approval)
                .where(
                    *in_session,
                    Approval.created_at == latest.scalar_subquery(),
                    Approval.status != ApprovalStatus.PENDING,
                )
                .order_by(Approval.updated_at)
            )
        ).all()
        # Only the latest batch's decided set names this continuation.
        if not decided or stream_id != _approval_continuation_stream_id(
            run_id, [row.tool_call_id for row in decided]
        ):
            return False
        logger.warning(
            "Resubmitting approval decisions whose submission stalled",
            session_id=str(session_id),
            run_id=str(run_id),
            tool_call_ids=[row.tool_call_id for row in decided],
        )
        await self.record_and_submit(
            session_id=session_id,
            expected_batch_id=None,
            decisions={
                row.tool_call_id: SubmittedDecision(
                    result=recorded_result(row),
                    metadata=recorded_metadata(row) or {},
                )
                for row in decided
            },
            approved_by=decided[-1].approved_by,
        )
        return True

    async def _announce_submitted_decisions(
        self,
        *,
        session_id: uuid.UUID,
        decision_set: _RecordedDecisionSet,
        resumed: bool,
        tool_call_ids: Sequence[str],
    ) -> ApprovalSubmissionResult:
        """Close a paused segment and tell open tabs which stream the run is on.

        Idempotent, so a replay that resubmits also finishes what a lost
        attempt left open. Holding the session row keeps a newer continuation
        from committing between the check and the feed event.
        """
        stream_id = decision_set.stream_id
        try:
            current_stream_id = await self.session.scalar(
                select(AgentSession.active_stream_id)
                .where(
                    AgentSession.id == session_id,
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.curr_run_id == decision_set.run_id,
                )
                .with_for_update(key_share=True)
            )
            # A later submission rotated past this set; it announces the run.
            newer_stream_id = (
                current_stream_id
                if current_stream_id not in (None, stream_id)
                else None
            )
            superseded = newer_stream_id is not None
            if superseded or not resumed:
                await self._emit_approval_idle_segment(
                    session_id=session_id,
                    stream_id=stream_id,
                    tool_call_ids=tool_call_ids,
                )
            if not superseded:
                await publish_session_event(
                    workspace_id=self.workspace_id,
                    session_id=session_id,
                    type=SessionFeedEventType.RUN_RESUMED
                    if resumed
                    else SessionFeedEventType.RUN_PAUSED,
                    run_id=decision_set.run_id,
                    stream_id=stream_id,
                )
            pending = (
                await self.session.scalars(
                    select(Approval.tool_call_id)
                    .where(
                        Approval.workspace_id == self.workspace_id,
                        Approval.session_id == session_id,
                        Approval.tool_call_id.in_(decision_set.batch_tool_call_ids),
                        Approval.status == ApprovalStatus.PENDING,
                    )
                    .order_by(Approval.tool_call_id)
                )
            ).all()
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return ApprovalSubmissionResult(
            run_id=decision_set.run_id,
            stream_id=newer_stream_id or stream_id,
            pending_tool_call_ids=list(pending),
            outcome="resumed" if resumed and not superseded else "submitted",
        )

    async def _approval_stream_items(
        self,
        *,
        session_id: uuid.UUID,
        tool_call_ids: Sequence[str],
    ) -> list[ToolCallContent]:
        """Return the active approval batch as stream items for UI replay.

        Only pending approvals plus the just-submitted ``tool_call_ids`` are
        replayed. Terminal approvals from earlier turns must stay out of the
        payload: the client drops an entire ``data-approval-request`` part when
        any contained tool call already has a terminal tool state, which would
        hide the still-pending cards.
        """
        stmt = (
            select(Approval)
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id == session_id,
                or_(
                    Approval.status == ApprovalStatus.PENDING,
                    Approval.tool_call_id.in_(tool_call_ids),
                ),
            )
            .order_by(Approval.created_at, Approval.id)
        )
        result = await self.session.execute(stmt)
        return [
            ToolCallContent(
                id=approval.tool_call_id,
                name=approval.tool_name,
                input=approval.tool_call_args or {},
                status=cast(ApprovalStreamStatus, approval.status.value),
                decision=approval.decision,
                reason=approval.reason,
            )
            for approval in result.scalars().all()
        ]

    async def _emit_approval_idle_segment(
        self,
        *,
        session_id: uuid.UUID,
        stream_id: uuid.UUID | None,
        tool_call_ids: Sequence[str],
    ) -> None:
        """Emit the latest approval state and a non-terminal Redis boundary."""
        if self.workspace_id is None:
            return
        stream = await AgentStream.new(
            workspace_id=self.workspace_id,
            session_id=session_id,
            stream_id=stream_id,
        )
        if approval_items := await self._approval_stream_items(
            session_id=session_id, tool_call_ids=tool_call_ids
        ):
            await stream.append(
                UnifiedStreamEvent.approval_request_event(approval_items).to_dict()
            )
        await stream.finish_idle_segment()

    async def _install_approval_continuation(
        self,
        agent_session: AgentSession,
        *,
        stream_id: uuid.UUID,
    ) -> None:
        """Point the session at the continuation stream; the caller commits.

        The marker lands before the pointer commits, so reconnects can wait on
        the stream while the decisions are submitted.
        """
        stream = await AgentStream.new(
            session_id=agent_session.id,
            workspace_id=self.workspace_id,
            stream_id=stream_id,
        )
        # A rejected submission of the same set may have left this stream closed.
        await stream.clear_buffer()
        await stream.mark_approval_continuation(
            submission_key=str(stream_id),
            previous_stream_id=agent_session.active_stream_id,
        )
        agent_session.active_stream_id = stream_id

    async def _revert_rejected_decisions(
        self,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        stream_id: uuid.UUID,
        previous_stream_id: uuid.UUID | None,
        tool_call_ids: Sequence[str],
    ) -> None:
        """Undo decisions the run refused: reopen their rows and restore the stream.

        Only while this set's stream is still current: a later submission
        recorded on top of these decisions and owns them now.
        """
        try:
            stream = await AgentStream.new(
                session_id=session_id,
                workspace_id=self.workspace_id,
                stream_id=stream_id,
            )
            await stream.done()
        except Exception as exc:
            logger.warning(
                "Failed to close rejected approval continuation stream",
                session_id=str(session_id),
                stream_id=str(stream_id),
                error=str(exc),
            )

        try:
            # Session row first: the same lock order as recording decisions.
            restored = await self.session.scalar(
                update(AgentSession)
                .where(
                    AgentSession.id == session_id,
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.curr_run_id == run_id,
                    AgentSession.active_stream_id == stream_id,
                )
                .values(active_stream_id=previous_stream_id)
                .returning(AgentSession.id)
                .execution_options(synchronize_session="fetch")
            )
            if restored is None:
                await self.session.commit()
                return
            await self.session.execute(
                update(Approval)
                .where(
                    Approval.workspace_id == self.workspace_id,
                    Approval.session_id == session_id,
                    Approval.tool_call_id.in_(tool_call_ids),
                )
                .values(
                    status=ApprovalStatus.PENDING,
                    reason=None,
                    decision=null(),
                    approved_by=None,
                    approved_at=None,
                    updated_at=func.now(),
                )
                .execution_options(synchronize_session="fetch")
            )
            await self.session.commit()
        except SQLAlchemyError as exc:
            await self.session.rollback()
            logger.error(
                "Failed to revert approval decisions the run rejected",
                session_id=str(session_id),
                tool_call_ids=list(tool_call_ids),
                error=str(exc),
            )
            # The rows still claim a set the run refused: not a clean rejection.
            raise AgentControlUncertain(
                "Rejected approval decisions could not be reverted"
            ) from None
