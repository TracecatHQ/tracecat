"""Linked-thread state for one durable agent run."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence

from temporalio import workflow
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.common.stream_types import ToolCallContent
    from tracecat.agent.delivery import (
        CHANNEL_DELIVERY_RETRY_POLICY,
        CHANNEL_DELIVERY_SCHEDULE_TO_CLOSE_TIMEOUT,
        CHANNEL_DELIVERY_START_TO_CLOSE_TIMEOUT,
        ApprovalDecided,
        ApprovalPending,
        ChannelNotice,
        DeliverChannelNoticeInput,
        TurnFinished,
        approval_batch_id,
        deliver_thread_notice,
    )
    from tracecat.auth.types import Role
    from tracecat.logger import logger
    from tracecat.temporal.patches import DurableAgentWorkflowPatch


class ThreadRun:
    """One run's linked-thread state; the workflow's lifecycle steps decide when to send."""

    def __init__(self, role: Role, session_id: uuid.UUID) -> None:
        self._role = role
        self._session_id = session_id
        # Set only for a linked run; None keeps every send a no-op.
        self._run_id: uuid.UUID | None = None
        # The open card's closing notice, sent once when the approval pause ends.
        self._open_card: ApprovalDecided | None = None
        # After one failed delivery, skip the rest so a dead provider stalls once.
        self._degraded = False

    def link(self, has_linked_thread: bool, run_id: uuid.UUID) -> None:
        # Histories recorded before delivery replay without any notice.
        if has_linked_thread and workflow.patched(
            DurableAgentWorkflowPatch.THREAD_DELIVERY
        ):
            self._run_id = run_id

    async def open_card(self, items: Sequence[ToolCallContent] | None) -> None:
        if self._run_id is None or not items:
            return
        batch_id = approval_batch_id(self._run_id, [item.id for item in items])
        ref = await self._send(
            ApprovalPending(
                session_id=self._session_id, run_id=self._run_id, batch_id=batch_id
            )
        )
        self._open_card = ApprovalDecided(
            session_id=self._session_id, batch_id=batch_id, previous_ref=ref
        )

    async def close_card(self) -> None:
        notice, self._open_card = self._open_card, None
        if notice is not None:
            await self._send(notice)

    async def turn_finished(self) -> None:
        if self._run_id is None:
            return
        try:
            await self._send(
                TurnFinished(session_id=self._session_id, run_id=self._run_id),
                at_turn_end=True,
            )
        except asyncio.CancelledError as exc:
            # Swallow even a cancel: finalize must still release the session.
            self._log_failure("turn_finished", exc)

    async def _send(
        self, notice: ChannelNotice, *, at_turn_end: bool = False
    ) -> str | None:
        if self._degraded:
            return None
        try:
            result = await workflow.execute_activity(
                deliver_thread_notice,
                DeliverChannelNoticeInput(role=self._role, notice=notice),
                schedule_to_close_timeout=CHANNEL_DELIVERY_SCHEDULE_TO_CLOSE_TIMEOUT,
                start_to_close_timeout=CHANNEL_DELIVERY_START_TO_CLOSE_TIMEOUT,
                retry_policy=CHANNEL_DELIVERY_RETRY_POLICY,
            )
        except ActivityError as exc:
            self._degraded = True
            # Mid-run, a workflow cancel must still end the run.
            if is_cancelled_exception(exc) and not at_turn_end:
                raise
            self._log_failure(notice.kind, exc)
            return None
        return result.ref

    def _log_failure(self, kind: str, exc: BaseException) -> None:
        logger.warning(
            "Failed to deliver thread notice",
            session_id=str(self._session_id),
            notice=kind,
            error=repr(exc),
        )
