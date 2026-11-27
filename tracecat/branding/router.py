"""Branding endpoints for the sign-in pages and the signed-in app."""

from fastapi import APIRouter, Query

from tracecat import config
from tracecat.api.common import get_default_organization_id
from tracecat.auth.dependencies import OrgUserRole
from tracecat.branding.schemas import BrandingRead
from tracecat.branding.service import get_effective_branding, resolve_org_id_by_slug
from tracecat.db.dependencies import AsyncDBSessionBypass

public_router = APIRouter(prefix="/auth", tags=["auth"])
router = APIRouter(prefix="/organization", tags=["organization"])


@public_router.get("/branding", response_model=BrandingRead)
async def get_public_branding(
    session: AsyncDBSessionBypass,
    org: str | None = Query(default=None, max_length=255),
) -> BrandingRead:
    """Branding for the sign-in and sign-up pages.

    Multi-tenant deployments only brand pages opened with ``?org=<slug>``;
    single-tenant deployments use the default organization.
    """
    if org:
        org_id = await resolve_org_id_by_slug(session, org.strip())
    elif config.TRACECAT__EE_MULTI_TENANT:
        org_id = None
    else:
        try:
            org_id = await get_default_organization_id(session)
        except ValueError:
            org_id = None
    if org_id is None:
        return BrandingRead()
    return await get_effective_branding(session, org_id)


@router.get("/branding", response_model=BrandingRead)
async def get_organization_branding(
    *,
    role: OrgUserRole,
    session: AsyncDBSessionBypass,
) -> BrandingRead:
    """Branding for the current organization, for any member."""
    if role.organization_id is None:
        return BrandingRead()
    return await get_effective_branding(session, role.organization_id)
