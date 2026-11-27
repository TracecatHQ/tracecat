"""Resolve the effective branding for an organization."""

from sqlalchemy import select

from tracecat.branding.schemas import BrandingRead
from tracecat.db.models import Organization
from tracecat.identifiers import OrganizationID
from tracecat.settings.schemas import BrandingSettingsRead, BrandingSettingsUpdate
from tracecat.settings.service import get_setting_from_bypass_session
from tracecat.tiers.access import SupportsExecute, is_org_entitled
from tracecat.tiers.enums import Entitlement


async def resolve_org_id_by_slug(
    session: SupportsExecute, slug: str
) -> OrganizationID | None:
    """Return the active organization with ``slug``, if any."""
    stmt = select(Organization.id).where(
        Organization.slug == slug,
        Organization.is_active.is_(True),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_effective_branding(
    session: SupportsExecute, org_id: OrganizationID
) -> BrandingRead:
    """Read an organization's branding through a caller-owned bypass session.

    Stored values are revalidated so a bad row falls back to the defaults
    instead of reaching the sign-in page.
    """
    if not await is_org_entitled(session, org_id, Entitlement.CUSTOM_BRANDING):
        return BrandingRead()
    values: dict[str, object] = {}
    for key in BrandingSettingsRead.keys():
        value = await get_setting_from_bypass_session(
            key, organization_id=org_id, session=session
        )
        if value is not None:
            values[key] = value
    try:
        settings = BrandingSettingsUpdate.model_validate(values)
    except ValueError:
        return BrandingRead()
    return BrandingRead(
        logo=settings.branding_logo,
        primary_color_light=settings.branding_primary_color_light,
        primary_color_dark=settings.branding_primary_color_dark,
        terms_enabled=settings.branding_terms_enabled,
    )
