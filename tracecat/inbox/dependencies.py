"""Inbox provider dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.inbox.types import InboxProvider


def get_inbox_provider(
    session: AsyncSession,
    role: Role,
) -> InboxProvider | None:
    """Get the inbox provider, if available.

    The inbox is sourced from agent runs, which is an EE feature loaded only if
    the tracecat_ee package is available.
    """
    try:
        from tracecat_ee.inbox.providers.agent_runs import AgentRunsInboxProvider

        logger.debug("Loaded AgentRunsInboxProvider")
        return AgentRunsInboxProvider(session, role)
    except ImportError:
        logger.debug("AgentRunsInboxProvider not available (EE feature)")
        return None
