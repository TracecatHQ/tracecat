"""Admin tier management service."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.logger import logger
from tracecat.service import BasePlatformService
from tracecat.tiers.exceptions import (
    CannotDeleteDefaultTierError,
    DefaultTierNotConfiguredError,
    OrganizationNotFoundError,
    TierInUseError,
    TierNotFoundError,
)
from tracecat.tiers.limits_cache import (
    invalidate_effective_limits_cache,
    invalidate_effective_limits_cache_many,
)
from tracecat.tiers.schemas import (
    OrganizationTierRead,
    OrganizationTierUpdate,
    TierCreate,
    TierRead,
    TierUpdate,
)


class AdminTierService(BasePlatformService):
    """Platform-level tier management."""

    service_name = "admin_tier"

    # Tier CRUD operations

    async def list_tiers(self, include_inactive: bool = False) -> Sequence[TierRead]:
        """List all tiers."""
        stmt = select(Tier).order_by(Tier.sort_order)
        if not include_inactive:
            stmt = stmt.where(Tier.is_active.is_(True))
        result = await self.session.execute(stmt)
        return TierRead.list_adapter().validate_python(result.scalars().all())

    async def get_tier(self, tier_id: uuid.UUID) -> TierRead:
        """Get tier by ID."""
        stmt = select(Tier).where(Tier.id == tier_id)
        result = await self.session.execute(stmt)
        tier = result.scalar_one_or_none()
        if not tier:
            raise TierNotFoundError(f"Tier {tier_id} not found")
        return TierRead.model_validate(tier)

    async def create_tier(self, params: TierCreate) -> TierRead:
        """Create a new tier."""
        # If this tier is set as default, unset other defaults
        if params.is_default:
            await self._unset_other_defaults(None)

        tier = Tier(
            display_name=params.display_name,
            max_concurrent_workflows=params.max_concurrent_workflows,
            max_action_executions_per_workflow=params.max_action_executions_per_workflow,
            max_concurrent_actions=params.max_concurrent_actions,
            api_rate_limit=params.api_rate_limit,
            api_burst_capacity=params.api_burst_capacity,
            entitlements=params.entitlements,
            is_default=params.is_default,
            sort_order=params.sort_order,
        )
        self.session.add(tier)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise TierNotFoundError(
                f"Tier with display_name '{params.display_name}' already exists"
            ) from e
        await self.session.refresh(tier)
        return TierRead.model_validate(tier)

    async def update_tier(self, tier_id: uuid.UUID, params: TierUpdate) -> TierRead:
        """Update a tier."""
        stmt = select(Tier).where(Tier.id == tier_id)
        result = await self.session.execute(stmt)
        tier = result.scalar_one_or_none()
        if not tier:
            raise TierNotFoundError(f"Tier {tier_id} not found")

        # If setting this tier as default, unset other defaults
        if params.is_default is True:
            await self._unset_other_defaults(tier_id)

        update_data = params.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(tier, field, value)

        await self.session.commit()
        await self.session.refresh(tier)
        org_ids = list(
            (
                await self.session.scalars(
                    select(OrganizationTier.organization_id).where(
                        OrganizationTier.tier_id == tier_id
                    )
                )
            ).all()
        )
        if org_ids:
            try:
                await invalidate_effective_limits_cache_many(org_ids)
            except Exception as e:
                logger.warning(
                    "Failed to invalidate effective limits cache for tier update",
                    tier_id=tier_id,
                    org_count=len(org_ids),
                    error=e,
                )
        return TierRead.model_validate(tier)

    async def delete_tier(self, tier_id: uuid.UUID) -> None:
        """Delete a tier (only if no orgs are assigned to it)."""
        # Check if tier exists
        stmt = select(Tier).where(Tier.id == tier_id)
        result = await self.session.execute(stmt)
        tier = result.scalar_one_or_none()
        if not tier:
            raise TierNotFoundError(f"Tier {tier_id} not found")

        # Prevent deleting the default tier
        if tier.is_default:
            raise CannotDeleteDefaultTierError

        # Check if any orgs are using this tier
        org_stmt = select(OrganizationTier).where(OrganizationTier.tier_id == tier_id)
        org_result = await self.session.execute(org_stmt)
        if org_result.first():
            raise TierInUseError(tier_id)

        await self.session.delete(tier)
        await self.session.commit()

    async def _unset_other_defaults(self, exclude_tier_id: uuid.UUID | None) -> None:
        """Unset is_default on all tiers except the specified one."""
        stmt = select(Tier).where(Tier.is_default.is_(True))
        if exclude_tier_id:
            stmt = stmt.where(Tier.id != exclude_tier_id)
        result = await self.session.execute(stmt)
        for tier in result.scalars().all():
            tier.is_default = False

    # Organization tier operations

    async def list_org_tiers(
        self, org_ids: Sequence[uuid.UUID] | None = None
    ) -> Sequence[OrganizationTierRead]:
        """List tier assignments for organizations."""
        if org_ids is not None and len(org_ids) == 0:
            return []

        stmt = select(OrganizationTier).options(selectinload(OrganizationTier.tier))
        if org_ids:
            stmt = stmt.where(OrganizationTier.organization_id.in_(org_ids))

        result = await self.session.execute(stmt)
        org_tiers = result.scalars().all()

        return [
            OrganizationTierRead(
                id=org_tier.id,
                organization_id=org_tier.organization_id,
                tier_id=org_tier.tier_id,
                max_concurrent_workflows=org_tier.max_concurrent_workflows,
                max_action_executions_per_workflow=org_tier.max_action_executions_per_workflow,
                max_concurrent_actions=org_tier.max_concurrent_actions,
                api_rate_limit=org_tier.api_rate_limit,
                api_burst_capacity=org_tier.api_burst_capacity,
                entitlement_overrides=org_tier.entitlement_overrides,
                stripe_customer_id=org_tier.stripe_customer_id,
                stripe_subscription_id=org_tier.stripe_subscription_id,
                expires_at=org_tier.expires_at,
                created_at=org_tier.created_at,
                updated_at=org_tier.updated_at,
                tier=TierRead.model_validate(org_tier.tier) if org_tier.tier else None,
            )
            for org_tier in org_tiers
        ]

    async def get_org_tier(self, org_id: uuid.UUID) -> OrganizationTierRead:
        """Get tier assignment for an organization."""
        # Verify org exists
        await self._verify_org_exists(org_id)

        stmt = (
            select(OrganizationTier)
            .options(selectinload(OrganizationTier.tier))
            .where(OrganizationTier.organization_id == org_id)
        )
        result = await self.session.execute(stmt)
        org_tier = result.scalar_one_or_none()

        if not org_tier:
            raise TierNotFoundError(
                f"No tier assignment found for organization {org_id}"
            )

        tier_read = TierRead.model_validate(org_tier.tier) if org_tier.tier else None
        return OrganizationTierRead(
            id=org_tier.id,
            organization_id=org_tier.organization_id,
            tier_id=org_tier.tier_id,
            max_concurrent_workflows=org_tier.max_concurrent_workflows,
            max_action_executions_per_workflow=org_tier.max_action_executions_per_workflow,
            max_concurrent_actions=org_tier.max_concurrent_actions,
            api_rate_limit=org_tier.api_rate_limit,
            api_burst_capacity=org_tier.api_burst_capacity,
            entitlement_overrides=org_tier.entitlement_overrides,
            stripe_customer_id=org_tier.stripe_customer_id,
            stripe_subscription_id=org_tier.stripe_subscription_id,
            expires_at=org_tier.expires_at,
            created_at=org_tier.created_at,
            updated_at=org_tier.updated_at,
            tier=tier_read,
        )

    async def update_org_tier(
        self, org_id: uuid.UUID, params: OrganizationTierUpdate
    ) -> OrganizationTierRead:
        """Update organization's tier assignment and overrides."""
        # Verify org exists
        await self._verify_org_exists(org_id)

        stmt = (
            select(OrganizationTier)
            .options(selectinload(OrganizationTier.tier))
            .where(OrganizationTier.organization_id == org_id)
        )
        result = await self.session.execute(stmt)
        org_tier = result.scalar_one_or_none()

        if not org_tier:
            # Create new org tier with default tier
            default_tier = await self._get_default_tier()
            org_tier = OrganizationTier(organization_id=org_id, tier_id=default_tier.id)
            self.session.add(org_tier)

        # If changing tier_id, verify the new tier exists
        if params.tier_id is not None:
            tier_stmt = select(Tier).where(Tier.id == params.tier_id)
            tier_result = await self.session.execute(tier_stmt)
            if not tier_result.scalar_one_or_none():
                raise TierNotFoundError(f"Tier {params.tier_id} not found")

        update_data = params.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(org_tier, field, value)

        await self.session.commit()
        try:
            await invalidate_effective_limits_cache(org_id)
        except Exception as e:
            logger.warning(
                "Failed to invalidate effective limits cache for organization tier update",
                org_id=org_id,
                error=e,
            )

        # Refresh with tier loaded
        stmt = (
            select(OrganizationTier)
            .options(selectinload(OrganizationTier.tier))
            .where(OrganizationTier.id == org_tier.id)
        )
        result = await self.session.execute(stmt)
        org_tier = result.scalar_one()

        tier_read = TierRead.model_validate(org_tier.tier) if org_tier.tier else None
        return OrganizationTierRead(
            id=org_tier.id,
            organization_id=org_tier.organization_id,
            tier_id=org_tier.tier_id,
            max_concurrent_workflows=org_tier.max_concurrent_workflows,
            max_action_executions_per_workflow=org_tier.max_action_executions_per_workflow,
            max_concurrent_actions=org_tier.max_concurrent_actions,
            api_rate_limit=org_tier.api_rate_limit,
            api_burst_capacity=org_tier.api_burst_capacity,
            entitlement_overrides=org_tier.entitlement_overrides,
            stripe_customer_id=org_tier.stripe_customer_id,
            stripe_subscription_id=org_tier.stripe_subscription_id,
            expires_at=org_tier.expires_at,
            created_at=org_tier.created_at,
            updated_at=org_tier.updated_at,
            tier=tier_read,
        )

    async def _get_default_tier(self) -> Tier:
        """Get the default tier, raising if not configured."""
        stmt = select(Tier).where(Tier.is_default.is_(True), Tier.is_active.is_(True))
        result = await self.session.execute(stmt)
        tier = result.scalar_one_or_none()
        if not tier:
            raise DefaultTierNotConfiguredError
        return tier

    async def _verify_org_exists(self, org_id: uuid.UUID) -> None:
        """Verify organization exists."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        if not result.scalar_one_or_none():
            raise OrganizationNotFoundError(org_id)
