"""Inbox domain types and protocols."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from tracecat.inbox.schemas import InboxItemRead
    from tracecat.pagination import CursorPaginatedResponse


class InboxItemType(StrEnum):
    """Types of inbox items."""

    APPROVAL = "approval"
    # Future types:
    # MENTION = "mention"
    # ASSIGNMENT = "assignment"


class InboxItemStatus(StrEnum):
    """Status of inbox items."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class InboxProvider(Protocol):
    """Protocol for inbox item providers.

    Providers are responsible for fetching inbox items from their respective
    domains (approvals, mentions, etc.) and transforming them into the unified
    InboxItemRead format.
    """

    async def list_items(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        reverse: bool = False,
        order_by: str | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List inbox items with cursor-based pagination."""
        ...
