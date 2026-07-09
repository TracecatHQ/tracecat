"""Session and organization resolution for the internal OIDC issuer.

Reuses the existing Tracecat session cookie and org resolution patterns
without duplicating the underlying auth logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum, auto

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.credentials import ACTIVE_ORG_COOKIE
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Organization, OrganizationMembership, User
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger


class NeedsAction(StrEnum):
    """Why session resolution could not produce a (user, org) pair."""

    LOGIN = auto()
    """User has no active session — redirect to sign-in."""


@dataclass(frozen=True)
class SessionResult:
    """Outcome of session + org resolution."""

    user: User
    organization_id: OrganizationID


@dataclass(frozen=True)
class SessionNeedsAction:
    """Session resolution requires user interaction."""

    action: NeedsAction


class OrgResolutionError(ValueError):
    """Raised when a user cannot be resolved to exactly one organization."""

    def __init__(self, message: str, *, membership_count: int) -> None:
        super().__init__(message)
        self.membership_count = membership_count


async def resolve_authorize_session(
    user: User | None,
    *,
    request: Request | None = None,
) -> SessionResult | SessionNeedsAction:
    """Resolve a Tracecat user and organization from the session.

    When request context is available, this mirrors the regular API auth
    resolver: honor the active-org cookie if it still points at an active
    membership, otherwise fall back to a stable active membership. That lets
    multi-org users complete MCP authorization while keeping the issued MCP
    token scoped to one organization.

    Args:
        user: The ``optional_current_active_user`` dependency result.
        request: Optional FastAPI request containing the active-org cookie.

    Returns:
        ``SessionResult`` on success, or ``SessionNeedsAction`` indicating
        what the caller must redirect the user to do.
    """
    if user is None:
        return SessionNeedsAction(action=NeedsAction.LOGIN)

    async with get_async_session_bypass_rls_context_manager() as session:
        return await _resolve_regular_user_org(session, user, request=request)


async def _resolve_regular_user_org(
    session: AsyncSession,
    user: User,
    *,
    request: Request | None = None,
) -> SessionResult:
    """Resolve org for a regular user from their memberships.

    With request context, honor the active-org cookie and otherwise choose a
    stable active membership, matching the regular API authentication path.
    Without request context, preserve the legacy exact-one behavior for callers
    that cannot make an explicit browser-org selection.

    Raises:
        OrgResolutionError: If the user has no active organization membership,
            or if there are multiple memberships without request context.
    """
    if request is not None and (cookie_value := request.cookies.get(ACTIVE_ORG_COOKIE)):
        try:
            cookie_org_id = uuid.UUID(cookie_value)
        except ValueError:
            cookie_org_id = None
        if cookie_org_id is not None:
            membership_result = await session.execute(
                select(OrganizationMembership.organization_id)
                .join(
                    Organization,
                    Organization.id == OrganizationMembership.organization_id,
                )
                .where(
                    OrganizationMembership.user_id == user.id,
                    OrganizationMembership.organization_id == cookie_org_id,
                    Organization.is_active.is_(True),
                )
            )
            if membership_result.scalar_one_or_none() is not None:
                return SessionResult(user=user, organization_id=cookie_org_id)

    result = await session.execute(
        select(OrganizationMembership.organization_id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            Organization.is_active.is_(True),
        )
        .order_by(Organization.created_at.asc(), Organization.id.asc())
    )
    org_ids = list(dict.fromkeys(result.scalars().all()))

    if len(org_ids) == 1:
        return SessionResult(user=user, organization_id=org_ids[0])

    if request is not None and len(org_ids) > 1:
        org_id = org_ids[0]
        logger.info(
            "MCP OIDC: selected stable org for multi-org user without active-org cookie",
            user_id=str(user.id),
            organization_id=str(org_id),
            org_count=len(org_ids),
        )
        return SessionResult(user=user, organization_id=org_id)

    if len(org_ids) == 0:
        logger.warning(
            "MCP OIDC: user has no active org memberships",
            user_id=str(user.id),
        )
    else:
        logger.warning(
            "MCP OIDC: user has multiple org memberships without request context",
            user_id=str(user.id),
            org_count=len(org_ids),
        )
    raise OrgResolutionError(
        f"User {user.email} cannot use MCP: "
        f"expected exactly 1 active organization membership, found {len(org_ids)}",
        membership_count=len(org_ids),
    )
