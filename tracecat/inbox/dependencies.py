"""Inbox provider dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.inbox.types import InboxProvider


def get_inbox_providers(
    session: AsyncSession,
    role: Role,
) -> list[InboxProvider]:
    """Get list of inbox providers.

    Providers are registered dynamically based on available features.
    EE features (like approvals) are loaded if the tracecat_ee package is available.
    """
    providers: list[InboxProvider] = []

    # EE: Add approvals provider if available
    try:
        from tracecat_ee.inbox.providers.approvals import ApprovalsInboxProvider

        providers.append(ApprovalsInboxProvider(session, role))
        logger.debug("Loaded ApprovalsInboxProvider")
    except ImportError:
        logger.debug("ApprovalsInboxProvider not available (EE feature)")

    return providers
