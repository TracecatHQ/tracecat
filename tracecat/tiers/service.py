"""Tier management service."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.service import BaseService
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.access import get_org_tier_and_resolved_tier
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

        In single-tenant mode, returns static OSS/self-host defaults.
        In multi-tenant mode, uses the org-assigned tier if present,
        otherwise falls back to the active default tier.

        Raises:
            DefaultTierNotConfiguredError: If no active default tier exists
                and the organization has no assigned tier.
        """
        if not config.TRACECAT__EE_MULTI_TENANT:
            return tier_defaults.DEFAULT_LIMITS.model_copy()

        org_tier, tier = await get_org_tier_and_resolved_tier(self.session, org_id)

        org_api_rate_limit = org_tier.api_rate_limit if org_tier is not None else None
        org_api_burst_capacity = (
            org_tier.api_burst_capacity if org_tier is not None else None
        )
        org_max_concurrent_workflows = (
            org_tier.max_concurrent_workflows if org_tier is not None else None
        )
        org_max_actions_per_workflow = (
            org_tier.max_action_executions_per_workflow
            if org_tier is not None
            else None
        )
        org_max_concurrent_actions = (
            org_tier.max_concurrent_actions if org_tier is not None else None
        )

        def resolve(org_value: int | None, tier_value: int | None) -> int | None:
            """Resolve value: org override takes precedence, then tier default."""
            return org_value if org_value is not None else tier_value

        return EffectiveLimits(
            api_rate_limit=resolve(org_api_rate_limit, tier.api_rate_limit),
            api_burst_capacity=resolve(org_api_burst_capacity, tier.api_burst_capacity),
            max_concurrent_workflows=resolve(
                org_max_concurrent_workflows,
                tier.max_concurrent_workflows,
            ),
            max_action_executions_per_workflow=resolve(
                org_max_actions_per_workflow,
                tier.max_action_executions_per_workflow,
            ),
            max_concurrent_actions=resolve(
                org_max_concurrent_actions,
                tier.max_concurrent_actions,
            ),
        )

    async def get_effective_entitlements(
        self, org_id: OrganizationID
    ) -> EffectiveEntitlements:
        """Get effective entitlements (org override or tier default).

        In single-tenant mode, returns static OSS/self-host defaults.
        In multi-tenant mode, uses the org-assigned tier if present,
        otherwise falls back to the active default tier.

        Raises:
            DefaultTierNotConfiguredError: If no active default tier exists
                and the organization has no assigned tier.
        """
        if not config.TRACECAT__EE_MULTI_TENANT:
            return tier_defaults.DEFAULT_ENTITLEMENTS.model_copy()

        org_tier, tier = await get_org_tier_and_resolved_tier(self.session, org_id)
        tier_entitlements = tier.entitlements or {}
        overrides = org_tier.entitlement_overrides or {} if org_tier is not None else {}

        def resolve_entitlement(key: str, default: bool = False) -> bool:
            """Resolve entitlement: org override takes precedence, then tier default."""
            if key in overrides:
                return overrides[key]
            return tier_entitlements.get(key, default)

        return EffectiveEntitlements(
            custom_registry=resolve_entitlement("custom_registry"),
            git_sync=resolve_entitlement("git_sync"),
            agent_addons=resolve_entitlement("agent_addons"),
            case_addons=resolve_entitlement("case_addons"),
            rbac=resolve_entitlement("rbac"),
        )
