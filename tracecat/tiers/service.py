"""Tier management service."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.service import BaseService
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.exceptions import (
    DefaultTierNotConfiguredError,
    OrganizationNotFoundError,
)
from tracecat.tiers.schemas import EffectiveEntitlements, EffectiveLimits

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID


class TierService(BaseService):
    """Manages organization tiers."""

    service_name = "tier"

    async def get_tier(self, tier_id: uuid.UUID) -> Tier | None:
        """Get tier by ID."""
        stmt = select(Tier).where(Tier.id == tier_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_default_tier(self) -> Tier | None:
        """Get the default tier for new organizations.

        Returns None if no default tier exists (migration hasn't run yet).
        """
        stmt = select(Tier).where(Tier.is_default.is_(True), Tier.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_org_tier(self, org_id: OrganizationID) -> OrganizationTier | None:
        """Get tier assignment for an organization."""
        stmt = (
            select(OrganizationTier)
            .options(selectinload(OrganizationTier.tier))
            .where(OrganizationTier.organization_id == org_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create_org_tier(self, org_id: OrganizationID) -> OrganizationTier:
        """Get existing org tier or create with default tier.

        If no OrganizationTier exists for the org, creates one with the default tier.
        """
        org_tier = await self.get_org_tier(org_id)
        if org_tier is not None:
            return org_tier

        # Get default tier - required for creating new org tiers
        default_tier = await self.get_default_tier()
        if default_tier is None:
            raise DefaultTierNotConfiguredError

        # Verify org exists
        org_stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(org_stmt)
        org = result.scalar_one_or_none()
        if org is None:
            raise OrganizationNotFoundError(org_id)

        org_tier = OrganizationTier(organization_id=org_id, tier_id=default_tier.id)
        self.session.add(org_tier)
        await self.session.commit()
        await self.session.refresh(org_tier)

        # Load the tier relationship
        stmt = (
            select(OrganizationTier)
            .options(selectinload(OrganizationTier.tier))
            .where(OrganizationTier.id == org_tier.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_effective_limits(self, org_id: OrganizationID) -> EffectiveLimits:
        """Get effective limits (org override or tier default).

        Falls back to DEFAULT_LIMITS if no tier is configured.
        """
        org_tier = await self.get_org_tier(org_id)

        if org_tier is None:
            # No org tier - return copy of default limits to avoid mutating shared instance
            return tier_defaults.DEFAULT_LIMITS.model_copy()

        tier = org_tier.tier

        def resolve(org_value: int | None, tier_value: int | None) -> int | None:
            """Resolve value: org override takes precedence, then tier default."""
            return org_value if org_value is not None else tier_value

        return EffectiveLimits(
            api_rate_limit=resolve(
                org_tier.api_rate_limit, tier.api_rate_limit if tier else None
            ),
            api_burst_capacity=resolve(
                org_tier.api_burst_capacity, tier.api_burst_capacity if tier else None
            ),
            max_concurrent_workflows=resolve(
                org_tier.max_concurrent_workflows,
                tier.max_concurrent_workflows if tier else None,
            ),
            max_action_executions_per_workflow=resolve(
                org_tier.max_action_executions_per_workflow,
                tier.max_action_executions_per_workflow if tier else None,
            ),
            max_concurrent_actions=resolve(
                org_tier.max_concurrent_actions,
                tier.max_concurrent_actions if tier else None,
            ),
        )

    async def get_effective_entitlements(
        self, org_id: OrganizationID
    ) -> EffectiveEntitlements:
        """Get effective entitlements (org override or tier default).

        Falls back to DEFAULT_ENTITLEMENTS if no tier is configured.
        """
        org_tier = await self.get_org_tier(org_id)

        if org_tier is None:
            # No org tier - return copy of default entitlements to avoid mutating shared instance
            return tier_defaults.DEFAULT_ENTITLEMENTS.model_copy()

        tier = org_tier.tier
        tier_entitlements = tier.entitlements if tier else {}
        overrides = org_tier.entitlement_overrides or {}

        def resolve_entitlement(key: str, default: bool = False) -> bool:
            """Resolve entitlement: org override takes precedence, then tier default."""
            if key in overrides:
                return overrides[key]
            return tier_entitlements.get(key, default)

        return EffectiveEntitlements(
            custom_registry=resolve_entitlement("custom_registry"),
            sso=resolve_entitlement("sso"),
            git_sync=resolve_entitlement("git_sync"),
            agent_approvals=resolve_entitlement("agent_approvals"),
            agent_presets=resolve_entitlement("agent_presets"),
            case_dropdowns=resolve_entitlement("case_dropdowns"),
            case_durations=resolve_entitlement("case_durations"),
            case_tasks=resolve_entitlement("case_tasks"),
            case_triggers=resolve_entitlement("case_triggers"),
        )
