"""Inbox provider dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.inbox.providers.agent_runs import AgentRunsInboxProvider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.inbox.types import InboxProvider


def get_inbox_provider(
    session: AsyncSession,
    role: Role,
) -> InboxProvider | None:
    """Get the inbox provider, sourced from agent runs."""
    return AgentRunsInboxProvider(session, role)
