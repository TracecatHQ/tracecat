"""Linked-thread notices for one durable agent run."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.common.stream_types import ToolCallContent
    from tracecat.agent.delivery import (
        ApprovalDecided,
        ApprovalPending,
        ChannelNotice,
        TurnFinished,
        approval_batch_id,
    )


class ThreadRun:
    """Which notices a linked run hands to each lifecycle activity to deliver.

    Pure workflow state: the activities deliver, so no step schedules a command
    of its own and unlinked runs replay unchanged.
    """

    def __init__(self, session_id: uuid.UUID) -> None:
        self._session_id = session_id
        # Set only for a linked run; None keeps every step's notices empty.
        self._run_id: uuid.UUID | None = None
        # The open card, closed once when the approval pause ends.
        self._card_batch_id: uuid.UUID | None = None
        self._card_ref: str | None = None

    def link(self, run_id: uuid.UUID) -> None:
        self._run_id = run_id

    def open_card(self, items: Sequence[ToolCallContent]) -> list[ChannelNotice]:
        if self._run_id is None:
            return []
        batch_id = approval_batch_id(self._run_id, [item.id for item in items])
        self._card_batch_id, self._card_ref = batch_id, None
        return [
            ApprovalPending(
                session_id=self._session_id, run_id=self._run_id, batch_id=batch_id
            )
        ]

    def card_posted(self, ref: str | None) -> None:
        self._card_ref = ref

    def close_card(self) -> list[ChannelNotice]:
        batch_id, ref = self._card_batch_id, self._card_ref
        self._card_batch_id = self._card_ref = None
        if batch_id is None:
            return []
        return [
            ApprovalDecided(
                session_id=self._session_id, batch_id=batch_id, previous_ref=ref
            )
        ]

    def turn_finished(self) -> list[ChannelNotice]:
        if self._run_id is None:
            return []
        # A card still open (cancelled or interrupted wait) closes first.
        return [
            *self.close_card(),
            TurnFinished(session_id=self._session_id, run_id=self._run_id),
        ]
