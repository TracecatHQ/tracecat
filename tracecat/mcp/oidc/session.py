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


async def resolve_authorize_session(
    user: User | None,
    *,
    request: Request | None = None,
    organization_hint: str | None = None,
) -> SessionResult | SessionNeedsAction:
    """Resolve a Tracecat user and organization for MCP authorization.

    Multi-org users are resolved from an explicit org hint or the same active-org
    cookie used by normal HTTP auth. Without either hint, the legacy single-org
    behavior is preserved and ambiguous users fail closed.

    Args:
        user: The ``optional_current_active_user`` dependency result.
        request: Optional request carrying the active-org cookie.
        organization_hint: Optional org slug or UUID from the authorization URL.

    Returns:
        ``SessionResult`` on success, or ``SessionNeedsAction`` indicating
        what the caller must redirect the user to do.
    """
    if user is None:
        return SessionNeedsAction(action=NeedsAction.LOGIN)

    async with get_async_session_bypass_rls_context_manager() as session:
        return await _resolve_regular_user_org(
            session,
            user,
            organization_hint=organization_hint,
            cookie_org_id=_read_active_org_cookie(request),
        )


def _read_active_org_cookie(request: Request | None) -> OrganizationID | None:
    """Return a syntactically valid active-org cookie value, if present."""
    if request is None:
        return None
    cookie_value = request.cookies.get(ACTIVE_ORG_COOKIE)
    if not cookie_value:
        return None
    try:
        return uuid.UUID(cookie_value)
    except ValueError:
        return None


async def _resolve_org_hint(
    session: AsyncSession,
    user: User,
    organization_hint: str,
) -> OrganizationID | None:
    """Resolve an org slug or UUID hint only if the user belongs to that org."""
    cleaned_hint = organization_hint.strip()
    if not cleaned_hint:
        return None

    try:
        hinted_org_id = uuid.UUID(cleaned_hint)
    except ValueError:
        hinted_org_id = None

    if hinted_org_id is not None:
        result = await session.execute(
            select(OrganizationMembership.organization_id)
            .join(
                Organization, Organization.id == OrganizationMembership.organization_id
            )
            .where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == hinted_org_id,
                Organization.is_active.is_(True),
            )
        )
        if org_id := result.scalar_one_or_none():
            return org_id

    result = await session.execute(
        select(OrganizationMembership.organization_id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            Organization.is_active.is_(True),
            Organization.slug == cleaned_hint,
        )
    )
    slug_org_ids = [row[0] for row in result.all()]
    if len(slug_org_ids) == 1:
        return slug_org_ids[0]
    if len(slug_org_ids) > 1:
        logger.warning(
            "MCP OIDC: org hint matched multiple active memberships",
            user_id=str(user.id),
            organization_hint=cleaned_hint,
            org_count=len(slug_org_ids),
        )
    return None


async def _resolve_cookie_org(
    session: AsyncSession,
    user: User,
    cookie_org_id: OrganizationID,
) -> OrganizationID | None:
    """Resolve an active-org cookie only if the user still belongs to that org."""
    result = await session.execute(
        select(OrganizationMembership.organization_id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == cookie_org_id,
            Organization.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _resolve_regular_user_org(
    session: AsyncSession,
    user: User,
    *,
    organization_hint: str | None = None,
    cookie_org_id: OrganizationID | None = None,
) -> SessionResult:
    """Resolve org for a regular user from their memberships.

    An explicit org hint or active-org cookie can disambiguate multi-org users.
    Without a hint, exactly one org must exist. Zero or multiple orgs is an
    error (fail closed).

    Raises:
        ValueError: If the user does not have exactly one organization membership.
    """
    normalized_org_hint = organization_hint.strip() if organization_hint else None
    if normalized_org_hint:
        org_id = await _resolve_org_hint(session, user, normalized_org_hint)
        if org_id is not None:
            return SessionResult(user=user, organization_id=org_id)
        logger.warning(
            "MCP OIDC: org hint does not match user membership",
            user_id=str(user.id),
            organization_hint=normalized_org_hint,
        )
        raise ValueError("Org hint does not match an active user membership")

    if cookie_org_id is not None:
        org_id = await _resolve_cookie_org(session, user, cookie_org_id)
        if org_id is not None:
            return SessionResult(user=user, organization_id=org_id)
        logger.warning(
            "MCP OIDC: active org cookie does not match user membership",
            user_id=str(user.id),
            cookie_org_id=str(cookie_org_id),
        )

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
        f"expected exactly 1 organization membership or explicit org context, "
        f"found {len(org_ids)}"
    )
