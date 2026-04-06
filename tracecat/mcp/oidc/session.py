"""Session and organization resolution for the internal OIDC issuer.

Reuses the existing Tracecat session cookie and org resolution patterns
without duplicating the underlying auth logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum, auto

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from tracecat import config
from tracecat.auth.credentials import ORG_OVERRIDE_COOKIE
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Organization, OrganizationMembership, User
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.organization.management import get_default_organization_id


class NeedsAction(StrEnum):
    """Why session resolution could not produce a (user, org) pair."""

    LOGIN = auto()
    """User has no active session — redirect to sign-in."""

    ORG_SELECTION = auto()
    """Superuser needs to pick an organization."""


@dataclass(frozen=True)
class SessionResult:
    """Outcome of session + org resolution."""

    user: User
    organization_id: OrganizationID


@dataclass(frozen=True)
class SessionNeedsAction:
    """Session resolution requires user interaction."""

    action: NeedsAction


async def resolve_authorize_session(
    request: Request,
    user: User | None,
) -> SessionResult | SessionNeedsAction:
    """Resolve a Tracecat user and their single organization from the session.

    Args:
        request: The incoming HTTP request (for cookie access).
        user: The ``optional_current_active_user`` dependency result.

    Returns:
        ``SessionResult`` on success, or ``SessionNeedsAction`` indicating
        what the caller must redirect the user to do.
    """
    if user is None:
        return SessionNeedsAction(action=NeedsAction.LOGIN)

    async with get_async_session_bypass_rls_context_manager() as session:
        if user.is_superuser:
            return await _resolve_superuser_org(request, session, user)
        return await _resolve_regular_user_org(session, user)


async def _resolve_superuser_org(
    request: Request,
    session: AsyncSession,
    user: User,
) -> SessionResult | SessionNeedsAction:
    """Resolve org for a platform superadmin.

    In single-tenant mode, uses the default org.
    In multi-tenant mode, requires the ``tracecat-org-id`` cookie.
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        org_id = await get_default_organization_id(session)
        logger.debug(
            "MCP OIDC: superuser resolved to default org (single-tenant)",
            user_id=str(user.id),
            organization_id=str(org_id),
        )
        return SessionResult(user=user, organization_id=org_id)

    # Multi-tenant: check cookie
    if org_cookie := request.cookies.get(ORG_OVERRIDE_COOKIE):
        try:
            candidate = uuid.UUID(org_cookie)
        except ValueError:
            logger.warning(
                "MCP OIDC: invalid org cookie format",
                org_cookie=org_cookie,
            )
            return SessionNeedsAction(action=NeedsAction.ORG_SELECTION)

        result = await session.execute(
            select(Organization.id).where(Organization.id == candidate)
        )
        if result.scalar_one_or_none() is not None:
            return SessionResult(user=user, organization_id=candidate)
        logger.warning(
            "MCP OIDC: org from cookie does not exist",
            org_id=str(candidate),
        )

    return SessionNeedsAction(action=NeedsAction.ORG_SELECTION)


async def _resolve_regular_user_org(
    session: AsyncSession,
    user: User,
) -> SessionResult:
    """Resolve org for a regular user from their memberships.

    Exactly one org must exist. Zero or multiple orgs is an error (fail closed).

    Raises:
        ValueError: If the user does not have exactly one organization membership.
    """
    result = await session.execute(
        select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == user.id
        )
    )
    org_ids = {row[0] for row in result.all()}

    if len(org_ids) == 1:
        org_id = next(iter(org_ids))
        return SessionResult(user=user, organization_id=org_id)

    if len(org_ids) == 0:
        logger.warning(
            "MCP OIDC: user has no org memberships",
            user_id=str(user.id),
        )
    else:
        logger.warning(
            "MCP OIDC: user has multiple org memberships (unsupported in v1)",
            user_id=str(user.id),
            org_count=len(org_ids),
        )
    raise ValueError(
        f"User {user.email} cannot use MCP: "
        f"expected exactly 1 organization membership, found {len(org_ids)}"
    )
