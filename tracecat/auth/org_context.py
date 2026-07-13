from __future__ import annotations

import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.api.common import get_default_organization_id
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Organization, OrganizationMembership
from tracecat.identifiers import OrganizationID

ACTIVE_ORG_COOKIE = "tracecat:active-org-id"


def parse_active_org_cookie(request: Request) -> uuid.UUID | None:
    """Parse the active organization cookie as a UUID when valid."""
    cookie_value = request.cookies.get(ACTIVE_ORG_COOKIE)
    if not cookie_value:
        return None
    try:
        return uuid.UUID(cookie_value)
    except ValueError:
        return None


async def resolve_active_org_id(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    preferred_org_id: uuid.UUID | None = None,
) -> OrganizationID | None:
    """Resolve an active organization membership for a user.

    The preferred organization ID is an untrusted hint re-validated against
    live memberships on every call. This helper performs pure SELECTs with no
    side effects and is safe under both RLS-scoped and RLS-bypass sessions.
    """
    if preferred_org_id is not None:
        membership_stmt = (
            select(OrganizationMembership.organization_id)
            .join(
                Organization,
                Organization.id == OrganizationMembership.organization_id,
            )
            .where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == preferred_org_id,
                Organization.is_active.is_(True),
            )
        )
        membership_row = (await session.execute(membership_stmt)).scalar_one_or_none()
        if membership_row is not None:
            return preferred_org_id

    org_mem_stmt = (
        select(OrganizationMembership.organization_id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user_id,
            Organization.is_active.is_(True),
        )
        .order_by(Organization.created_at.asc(), Organization.id.asc())
    )
    org_membership_result = await session.execute(org_mem_stmt)
    return org_membership_result.scalars().first()


async def _resolve_by_slug(
    session: AsyncSession, org_slug: str
) -> OrganizationID | None:
    stmt = select(Organization.id).where(
        Organization.slug == org_slug,
        Organization.is_active.is_(True),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def resolve_auth_organization_id(
    request: Request,
    *,
    session: AsyncSession | None = None,
) -> OrganizationID:
    """Resolve target org for pre-auth flows.

    In multi-tenant mode, pre-auth flows require explicit org selection via
    `?org=<slug>`.
    """
    if session is None:
        async with get_async_session_bypass_rls_context_manager() as local_session:
            return await resolve_auth_organization_id(request, session=local_session)

    if not config.TRACECAT__EE_MULTI_TENANT:
        return await get_default_organization_id(session)

    org_slug = request.query_params.get("org")
    if org_slug:
        org_id = await _resolve_by_slug(session, org_slug.strip())
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid organization",
            )
        return org_id

    raise HTTPException(
        status_code=status.HTTP_428_PRECONDITION_REQUIRED,
        detail="Organization selection required",
    )
