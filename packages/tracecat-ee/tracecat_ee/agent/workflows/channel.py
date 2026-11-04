"""Channel delivery notices sent by the durable agent workflow."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence

from temporalio import workflow
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.approvals.batch import approval_batch_id
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
        deliver_channel_notice,
    )
    from tracecat.auth.types import Role
    from tracecat.logger import logger
    from tracecat.temporal.patches import DurableAgentWorkflowPatch


class ChannelNotifier:
    """Sends one run's notices; inert until a linked thread is captured."""

    def __init__(self, role: Role, session_id: uuid.UUID) -> None:
        self._role = role
        self._session_id = session_id
        # Set only for a linked session; None keeps every notice inert.
        self._run_id: uuid.UUID | None = None
        self._decided: ApprovalDecided | None = None

    def capture(self, linked: bool, run_id: uuid.UUID) -> None:
        # Histories recorded before delivery replay without any notice.
        if linked and workflow.patched(DurableAgentWorkflowPatch.CHANNEL_DELIVERY):
            self._run_id = run_id

    async def turn_finished(self) -> None:
        if self._run_id is None:
            return
        notice = TurnFinished(session_id=self._session_id, run_id=self._run_id)
        try:
            await self._deliver(notice)
        except (ActivityError, asyncio.CancelledError) as exc:
            # Swallow even a cancel: finalize must still release the session.
            self._log_failure(notice, exc)

    async def approval_pending(self, items: Sequence[ToolCallContent] | None) -> None:
        if self._run_id is None or not items:
            return
        batch_id = approval_batch_id(self._run_id, [item.id for item in items])
        notice = ApprovalPending(
            session_id=self._session_id, run_id=self._run_id, batch_id=batch_id
        )
        self._decided = ApprovalDecided(
            session_id=self._session_id,
            batch_id=batch_id,
            previous_ref=await self._deliver_mid_run(notice),
        )

    async def approval_decided(self) -> None:
        notice, self._decided = self._decided, None
        if notice is not None:
            await self._deliver_mid_run(notice)

    async def _deliver_mid_run(self, notice: ChannelNotice) -> str | None:
        try:
            return await self._deliver(notice)
        except ActivityError as exc:
            # A workflow cancel must still end the run.
            if is_cancelled_exception(exc):
                raise
            self._log_failure(notice, exc)
            return None

    async def _deliver(self, notice: ChannelNotice) -> str | None:
        result = await workflow.execute_activity(
            deliver_channel_notice,
            DeliverChannelNoticeInput(role=self._role, notice=notice),
            schedule_to_close_timeout=CHANNEL_DELIVERY_SCHEDULE_TO_CLOSE_TIMEOUT,
            start_to_close_timeout=CHANNEL_DELIVERY_START_TO_CLOSE_TIMEOUT,
            retry_policy=CHANNEL_DELIVERY_RETRY_POLICY,
        )
        return result.ref

    def _log_failure(self, notice: ChannelNotice, exc: BaseException) -> None:
        logger.warning(
            "Failed to deliver channel notice",
            session_id=str(self._session_id),
            notice=notice.kind,
            error=repr(exc),
        )
