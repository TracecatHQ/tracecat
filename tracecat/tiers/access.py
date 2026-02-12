"""Shared entitlement access helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.db.models import OrganizationTier, Tier
from tracecat.exceptions import EntitlementRequired
from tracecat.identifiers import OrganizationID
from tracecat.tiers.enums import Entitlement


async def is_org_entitled(
    session: AsyncSession,
    org_id: OrganizationID,
    entitlement: Entitlement,
) -> bool:
    """Check if an organization has a specific entitlement."""
    stmt = (
        select(OrganizationTier)
        .options(selectinload(OrganizationTier.tier))
        .where(OrganizationTier.organization_id == org_id)
    )
    result = await session.execute(stmt)
    org_tier = result.scalar_one_or_none()

    if org_tier is None:
        default_tier_stmt = select(Tier).where(
            Tier.is_default.is_(True), Tier.is_active.is_(True)
        )
        default_tier_result = await session.execute(default_tier_stmt)
        default_tier = default_tier_result.scalar_one_or_none()
        if default_tier is None:
            return False
        tier_entitlements = default_tier.entitlements or {}
        return bool(tier_entitlements.get(entitlement.value, False))

    overrides = org_tier.entitlement_overrides or {}
    override = overrides.get(entitlement.value)
    if override is not None:
        return bool(override)

    tier = org_tier.tier
    tier_entitlements = tier.entitlements if tier is not None else {}
    return bool(tier_entitlements.get(entitlement.value, False))


async def require_org_entitlement(
    session: AsyncSession,
    org_id: OrganizationID,
    entitlement: Entitlement,
) -> None:
    """Require a specific organization entitlement."""
    if not await is_org_entitled(session, org_id, entitlement):
        raise EntitlementRequired(entitlement.value)
