"""Inbox domain types and protocols."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from tracecat.agent.session.types import AgentSessionEntity
    from tracecat.inbox.schemas import InboxItemRead
    from tracecat.pagination import CursorPaginatedResponse


class InboxItemType(StrEnum):
    """Types of inbox items."""

    APPROVAL = "approval"
    AGENT_RUN = "agent_run"
    # Future types:
    # MENTION = "mention"
    # ASSIGNMENT = "assignment"


class InboxItemStatus(StrEnum):
    """Status of inbox items."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class InboxGroup(StrEnum):
    """Display groups for inbox items.

    Groups are derived from approval state and live workflow execution status,
    so membership cannot be expressed as a pure SQL filter.
    """

    REVIEW_REQUIRED = "review_required"
    RUNNING = "running"
    ERROR = "error"
    COMPLETED = "completed"


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
        search: str | None = None,
        group: InboxGroup | None = None,
        entity_type: AgentSessionEntity | None = None,
        created_after: datetime | None = None,
        updated_after: datetime | None = None,
    ) -> CursorPaginatedResponse[InboxItemRead]:
        """List inbox items with cursor-based pagination."""
        ...

    async def count_pending_items(self) -> int:
        """Count pending inbox items that require attention."""
        ...
