"""Approval queue domain types and protocols."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from tracecat.approvals.schemas import ApprovalItemRead
    from tracecat.pagination import CursorPaginatedResponse


class ApprovalItemType(StrEnum):
    """Types of approval items."""

    APPROVAL = "approval"
    # Future types:
    # MENTION = "mention"
    # ASSIGNMENT = "assignment"


class ApprovalItemStatus(StrEnum):
    """Status of approval items."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalProvider(Protocol):
    """Protocol for approval item providers.

    Providers are responsible for fetching approval items from their respective
    domains (approvals, mentions, etc.) and transforming them into the unified
    ApprovalItemRead format.
    """

    async def list_approvals(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        reverse: bool = False,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[ApprovalItemRead]:
        """List approval items with cursor-based pagination."""
        ...
