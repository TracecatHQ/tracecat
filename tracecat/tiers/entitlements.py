"""Entitlement checks for feature gating by tier."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from tracecat.exceptions import EntitlementRequired
from tracecat.tiers.service import TierService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role
    from tracecat.identifiers import OrganizationID


class Entitlement(StrEnum):
    """Available feature entitlements."""

    CUSTOM_REGISTRY = "custom_registry"
    SSO = "sso"
    GIT_SYNC = "git_sync"


class EntitlementService:
    """Checks feature entitlements for organizations.

    Usage:
        async with TierService.with_session(role=role) as tier_svc:
            entitlement_svc = EntitlementService(tier_svc)
            await entitlement_svc.check_entitlement(org_id, Entitlement.CUSTOM_REGISTRY)
    """

    def __init__(self, tier_service: TierService):
        self.tier_service = tier_service

    async def is_entitled(
        self, org_id: OrganizationID, entitlement: Entitlement
    ) -> bool:
        """Check if an organization has a specific entitlement.

        Args:
            org_id: The organization ID to check
            entitlement: The entitlement to check for

        Returns:
            True if the organization is entitled to the feature, False otherwise
        """
        effective = await self.tier_service.get_effective_entitlements(org_id)
        return getattr(effective, entitlement.value, False)

    async def check_entitlement(
        self, org_id: OrganizationID, entitlement: Entitlement
    ) -> None:
        """Check if an organization has an entitlement, raising if not.

        Args:
            org_id: The organization ID to check
            entitlement: The entitlement to require

        Raises:
            EntitlementRequired: If the organization is not entitled to the feature
        """
        if not await self.is_entitled(org_id, entitlement):
            raise EntitlementRequired(entitlement.value)


async def check_entitlement(
    session: AsyncSession,
    role: Role,
    entitlement: Entitlement,
) -> None:
    """Convenience function to check entitlement in a single call.

    Args:
        session: Database session
        role: The current role (must have organization_id)
        entitlement: The entitlement to require

    Raises:
        EntitlementRequired: If the organization is not entitled to the feature
        ValueError: If the role has no organization_id
    """
    if role.organization_id is None:
        raise ValueError("Role must have organization_id to check entitlements")
    tier_svc = TierService(session, role=role)
    entitlement_svc = EntitlementService(tier_svc)
    await entitlement_svc.check_entitlement(role.organization_id, entitlement)
