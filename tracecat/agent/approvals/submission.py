"""Submit approval decisions to the waiting run, then record them on their rows."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from tracecat.agent.approvals.decisions import (
    SubmittedDecision,
    decision_matches_row,
    decision_outcome,
    record_decision,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.registry import get_agent_backend
from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import AgentControlRejected
from tracecat.agent.channel_publisher import approval_batch_id
from tracecat.agent.common.stream_types import (
    ApprovalStreamStatus,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.feed import SessionFeedEventType, publish_session_event
from tracecat.db.locks import derive_lock_key_from_parts, pg_advisory_xact_lock
from tracecat.db.models import AgentSession, Approval
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from tracecat_ee.agent.approvals.service import ApprovalResult


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
class _SubmissionAttempt:
    """Undecided decisions with their continuation installed, ready to submit."""

    run_id: uuid.UUID
    backend: AgentBackend[Any, Any]
    stream_id: uuid.UUID
    previous_stream_id: uuid.UUID | None
    decisions: dict[str, SubmittedDecision]
    batch_tool_call_ids: list[str]
    submission: WorkflowApprovalSubmission


@dataclass(frozen=True, slots=True)
class ApprovalSubmissionResult:
    """What one submission delivered to the waiting run."""

    run_id: uuid.UUID
    # The stream carrying the run's output for this caller to follow; None
    # when the call was a ``noop`` and the session has no stream.
    stream_id: uuid.UUID | None
    pending_tool_call_ids: list[str]
    # ``noop``: every decision was already recorded, so the run has it;
    # ``submitted``: the run took the decisions but this call didn't resume it;
    # ``resumed``: this call resumed the run and owns announcing it.
    outcome: Literal["noop", "submitted", "resumed"]


def _approval_continuation_stream_id(
    run_id: uuid.UUID, decisions: Mapping[str, ApprovalResult]
) -> uuid.UUID:
    """Name the continuation that submits one set of decisions.

    A retry of the same decisions derives the same stream, and so the same
    Temporal update id: the run applies it at most once. A different decision
    derives another, which the run refuses.
    """
    outcomes = sorted(
        (tool_call_id, decision_outcome(result))
        for tool_call_id, result in decisions.items()
    )
    return uuid.uuid5(run_id, json.dumps(outcomes, sort_keys=True))


class ApprovalSubmissionService(BaseWorkspaceService):
    """Submits approval decisions to the waiting run, then records them."""

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

    async def _begin_submission(
        self,
        *,
        session_id: uuid.UUID,
        expected_batch_id: uuid.UUID | None,
        decisions: Mapping[str, SubmittedDecision],
        approved_by: uuid.UUID | None,
    ) -> _SubmissionAttempt | ApprovalSubmissionResult:
        """Install the continuation for the undecided decisions, in one commit.

        Returns a ``noop`` result when every decision is already recorded.
        """
        try:
            batch = await self._lock_decided_batch(
                session_id=session_id,
                expected_batch_id=expected_batch_id,
                decisions=decisions,
            )
            by_id = {row.tool_call_id: row for row in batch.rows}
            pending_ids = [
                row.tool_call_id
                for row in batch.rows
                if row.status is ApprovalStatus.PENDING
            ]
            undecided = {
                tool_call_id: decision
                for tool_call_id, decision in decisions.items()
                if by_id[tool_call_id].status is ApprovalStatus.PENDING
            }
            previous_stream_id = batch.agent_session.active_stream_id
            if not undecided:
                # A row is decided only after the run accepted it: a repeat.
                await self.session.commit()
                return ApprovalSubmissionResult(
                    run_id=batch.run_id,
                    stream_id=previous_stream_id,
                    pending_tool_call_ids=pending_ids,
                    outcome="noop",
                )
            stream_id = _approval_continuation_stream_id(
                batch.run_id,
                {
                    tool_call_id: decision.result
                    for tool_call_id, decision in undecided.items()
                },
            )
            if previous_stream_id != stream_id:
                await self._install_approval_continuation(
                    batch.agent_session, stream_id=stream_id
                )
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

        return _SubmissionAttempt(
            run_id=batch.run_id,
            backend=batch.backend,
            stream_id=stream_id,
            previous_stream_id=previous_stream_id,
            decisions=undecided,
            batch_tool_call_ids=[row.tool_call_id for row in batch.rows],
            submission=WorkflowApprovalSubmission(
                approvals={
                    tool_call_id: decision.result
                    for tool_call_id, decision in undecided.items()
                },
                approved_by=approved_by,
                decision_metadata={
                    tool_call_id: decision.metadata
                    for tool_call_id, decision in undecided.items()
                    if decision.metadata
                }
                or None,
                new_stream_id=stream_id,
            ),
        )

    async def submit_and_record(
        self,
        *,
        session_id: uuid.UUID,
        expected_batch_id: uuid.UUID | None,
        decisions: Mapping[str, SubmittedDecision],
        approved_by: uuid.UUID | None,
    ) -> ApprovalSubmissionResult:
        """Submit decisions to the waiting run, then record them on their rows.

        The run holds the decisions; the rows follow it. A row is written only
        once the run accepted its decision, so a decided row never names a
        decision the run lacks. A crash before the write leaves the row pending
        until a retry or the run's own write at resume catches it up.
        Submissions use an update id derived from their decisions, so a retry
        is applied at most once. Submitters take turns, so the run and the
        session agree on which submission came last.

        Raises:
            TracecatNotFoundError: If the session or its run is gone.
            ApprovalNoLongerActiveError: If ``expected_batch_id`` is not the
                current run's batch, or a decision contradicts a recorded one.
            ApprovalDecisionInvalidError: If an ID is not an approval of the
                session.
            AgentControlRejected: If the run refused the decisions, for one
                because another decision for a tool call reached it first.
            AgentControlUncertain: If the outcome is unknown; retry the same
                decisions.
        """
        attempt = await self._begin_submission(
            session_id=session_id,
            expected_batch_id=expected_batch_id,
            decisions=decisions,
            approved_by=approved_by,
        )
        if isinstance(attempt, ApprovalSubmissionResult):
            return attempt

        logger.info(
            "Submitting approval decisions",
            session_id=str(session_id),
            run_id=str(attempt.run_id),
            submitted=sorted(attempt.decisions),
        )
        try:
            # Held until the decisions are recorded: the last accepted
            # submission also names the stream the run writes to.
            await pg_advisory_xact_lock(
                self.session,
                derive_lock_key_from_parts("approval-submission", str(session_id)),
            )
            resumed = await attempt.backend.submit_approvals(
                attempt.run_id, attempt.submission
            )
        except AgentControlRejected:
            await self.session.rollback()
            await self._restore_rejected_stream(
                session_id=session_id,
                run_id=attempt.run_id,
                stream_id=attempt.stream_id,
                previous_stream_id=attempt.previous_stream_id,
            )
            raise
        except BaseException:
            await self.session.rollback()
            raise

        await self._record_accepted_decisions(
            session_id=session_id, attempt=attempt, approved_by=approved_by
        )
        return await self._announce_submitted_decisions(
            session_id=session_id,
            attempt=attempt,
            resumed=resumed,
            tool_call_ids=list(decisions),
        )

    async def _record_accepted_decisions(
        self,
        *,
        session_id: uuid.UUID,
        attempt: _SubmissionAttempt,
        approved_by: uuid.UUID | None,
    ) -> None:
        """Point the session at the accepted stream and record its decisions.

        Best effort: the run already holds the decisions and writes every row
        at resume.
        """
        decisions = attempt.decisions
        try:
            # The run writes to the stream of the last submission it accepted.
            await self.session.execute(
                update(AgentSession)
                .where(
                    AgentSession.id == session_id,
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.curr_run_id == attempt.run_id,
                )
                .values(active_stream_id=attempt.stream_id)
                .execution_options(synchronize_session="fetch")
            )
            rows = (
                await self.session.scalars(
                    select(Approval)
                    .where(
                        Approval.workspace_id == self.workspace_id,
                        Approval.session_id == session_id,
                        Approval.tool_call_id.in_(list(decisions)),
                        Approval.status == ApprovalStatus.PENDING,
                    )
                    .order_by(Approval.tool_call_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
            for row in rows:
                record_decision(
                    row, decisions[row.tool_call_id], approved_by=approved_by
                )
            await self.session.commit()
        except SQLAlchemyError as exc:
            await self.session.rollback()
            logger.warning(
                "Failed to record approval decisions the run accepted",
                session_id=str(session_id),
                tool_call_ids=sorted(decisions),
                error=str(exc),
            )

    async def _announce_submitted_decisions(
        self,
        *,
        session_id: uuid.UUID,
        attempt: _SubmissionAttempt,
        resumed: bool,
        tool_call_ids: Sequence[str],
    ) -> ApprovalSubmissionResult:
        """Close a paused segment and tell open tabs which stream the run is on.

        Holding the session row keeps a newer continuation from committing
        between the check and the feed event.
        """
        stream_id = attempt.stream_id
        try:
            current_stream_id = await self.session.scalar(
                select(AgentSession.active_stream_id)
                .where(
                    AgentSession.id == session_id,
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.curr_run_id == attempt.run_id,
                )
                .with_for_update(key_share=True)
            )
            # A later submission rotated past this one; it announces the run.
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
                    run_id=attempt.run_id,
                    stream_id=stream_id,
                )
            pending = (
                await self.session.scalars(
                    select(Approval.tool_call_id)
                    .where(
                        Approval.workspace_id == self.workspace_id,
                        Approval.session_id == session_id,
                        Approval.tool_call_id.in_(attempt.batch_tool_call_ids),
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
            run_id=attempt.run_id,
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
        # A rejected submission of the same decisions may have left this stream closed.
        await stream.clear_buffer()
        await stream.mark_approval_continuation(
            submission_key=str(stream_id),
            previous_stream_id=agent_session.active_stream_id,
        )
        agent_session.active_stream_id = stream_id

    async def _restore_rejected_stream(
        self,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        stream_id: uuid.UUID,
        previous_stream_id: uuid.UUID | None,
    ) -> None:
        """Close the refused continuation and point the session back.

        Only while this continuation is still current: a later submission
        rotated past it and owns the pointer now. Nothing was recorded, so
        nothing else needs undoing.
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
            await self.session.execute(
                update(AgentSession)
                .where(
                    AgentSession.id == session_id,
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.curr_run_id == run_id,
                    AgentSession.active_stream_id == stream_id,
                )
                .values(active_stream_id=previous_stream_id)
                .execution_options(synchronize_session="fetch")
            )
            await self.session.commit()
        except SQLAlchemyError as exc:
            await self.session.rollback()
            logger.warning(
                "Failed to restore the stream of a rejected approval submission",
                session_id=str(session_id),
                stream_id=str(stream_id),
                error=str(exc),
            )
