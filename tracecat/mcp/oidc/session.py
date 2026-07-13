"""Session and organization resolution for the internal OIDC issuer.

Organization resolution mirrors the HTTP API path: the active-org cookie is a
re-validated hint, with a stable oldest-active-org fallback. This scopes MCP
tokens to one organization while allowing multi-org users to authorize.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from fastapi import Request

from tracecat.auth.org_context import parse_active_org_cookie, resolve_active_org_id
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
    """Raised when a user cannot be resolved to exactly one organization."""

    def __init__(self, message: str, *, membership_count: int) -> None:
        super().__init__(message)
        self.membership_count = membership_count


async def resolve_authorize_session(
    user: User | None,
    request: Request,
) -> SessionResult | SessionNeedsAction:
    """Resolve a Tracecat user and one active organization from the session.

    The active-org cookie is re-validated against live memberships. If it is
    absent or invalid, the user's oldest active organization is selected.

    Args:
        user: The ``optional_current_active_user`` dependency result.
        request: The request carrying the optional active-org cookie.

    Returns:
        ``SessionResult`` on success, or ``SessionNeedsAction`` indicating
        what the caller must redirect the user to do.
    """
    if user is None:
        return SessionNeedsAction(action=NeedsAction.LOGIN)

    async with get_async_session_bypass_rls_context_manager() as session:
        org_id = await resolve_active_org_id(
            session,
            user.id,
            preferred_org_id=parse_active_org_cookie(request),
        )
    if org_id is None:
        logger.warning(
            "MCP OIDC: user has no active org memberships",
            user_id=str(user.id),
        )
        raise OrgResolutionError(
            f"User {user.email} cannot use MCP: no active organization membership",
            membership_count=0,
        )
    return SessionResult(user=user, organization_id=org_id)
