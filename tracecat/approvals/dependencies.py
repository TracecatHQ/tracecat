"""Approval provider dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.approvals.types import ApprovalProvider
    from tracecat.auth.types import Role


def get_approval_providers(
    session: AsyncSession,
    role: Role,
) -> list[ApprovalProvider]:
    """Get list of approval providers.

    Providers are registered dynamically based on available features.
    EE features (like approvals) are loaded if the tracecat_ee package is available.
    """
    providers: list[ApprovalProvider] = []

    # EE: Add approvals provider if available
    try:
        from tracecat_ee.approvals.providers.approvals import ApprovalsProvider

        providers.append(ApprovalsProvider(session, role))
        logger.debug("Loaded ApprovalsProvider")
    except ImportError:
        logger.debug("ApprovalsProvider not available (EE feature)")

    return providers
