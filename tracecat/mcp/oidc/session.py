"""Session and organization resolution for the internal OIDC issuer.

Reuses the existing Tracecat session cookie and org resolution patterns
without duplicating the underlying auth logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.credentials import _resolve_org_for_regular_user
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import User
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
    """Raised when a user cannot be resolved to an organization."""


async def resolve_authorize_session(
    request: Request,
    user: User | None,
) -> SessionResult | SessionNeedsAction:
    """Resolve a Tracecat user and their active organization from the session.

    Args:
        request: The authorize request, used for the active-org cookie.
        user: The ``optional_current_active_user`` dependency result.

    Returns:
        ``SessionResult`` on success, or ``SessionNeedsAction`` indicating
        what the caller must redirect the user to do.
    """
    if user is None:
        return SessionNeedsAction(action=NeedsAction.LOGIN)

    async with get_async_session_bypass_rls_context_manager() as session:
        return await _resolve_regular_user_org(request, session, user)


async def _resolve_regular_user_org(
    request: Request,
    session: AsyncSession,
    user: User,
) -> SessionResult:
    """Resolve the org the user is currently working in.

    Uses the same resolution as the HTTP API: the ``tracecat:active-org-id``
    cookie set by the org switcher when the user is a member of that active
    org, otherwise the user's oldest active org membership.

    Raises:
        OrgResolutionError: If the user has no active organization membership.
    """
    try:
        org_id = await _resolve_org_for_regular_user(request, session, user)
    except HTTPException as exc:
        logger.warning(
            "MCP OIDC: user has no org memberships",
            user_id=str(user.id),
        )
        raise OrgResolutionError(
            f"User {user.email} cannot use MCP: no organization membership"
        ) from exc
    return SessionResult(user=user, organization_id=org_id)
