from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.api.common import get_default_organization_id
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Organization
from tracecat.identifiers import OrganizationID


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
