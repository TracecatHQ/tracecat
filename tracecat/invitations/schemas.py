"""Shared API response schemas for bulk invitations.

Organization and workspace bulk-invite responses are field-for-field identical,
so both routers return the same models defined here.
"""

from __future__ import annotations

from pydantic import BaseModel

from tracecat.invitations.types import BatchInviteItem, BatchInviteStatus


class BatchInvitationItemResult(BaseModel):
    """Per-email outcome of a bulk invitation request."""

    email: str
    status: BatchInviteStatus
    reason: str | None = None


class InvitationBatchResult(BaseModel):
    """Response model for a bulk invitation request."""

    results: list[BatchInvitationItemResult]
    created_count: int
    skipped_count: int


def build_batch_result(items: list[BatchInviteItem]) -> InvitationBatchResult:
    """Assemble the bulk-invite response from per-email outcomes."""
    created_count = sum(1 for i in items if i.status == BatchInviteStatus.CREATED)
    return InvitationBatchResult(
        results=[
            BatchInvitationItemResult(email=i.email, status=i.status, reason=i.reason)
            for i in items
        ],
        created_count=created_count,
        skipped_count=len(items) - created_count,
    )
