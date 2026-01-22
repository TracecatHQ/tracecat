"""Organization management service for admin control plane."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.db.models import Organization
from tracecat.ee.compute.schemas import Tier
from tracecat.service import BaseService
from tracecat_ee.admin.organizations.schemas import (
    OrgCreate,
    OrgRead,
    OrgUpdate,
    OrgUpdateTier,
)
from tracecat_ee.admin.organizations.types import TierChangeResult

if TYPE_CHECKING:
    from tracecat_ee.compute.manager import WorkerPoolManager


class AdminOrgService(BaseService):
    """Platform-level organization management."""

    service_name = "admin_org"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._worker_pool_manager: WorkerPoolManager | None = None

    @property
    def worker_pool_manager(self) -> WorkerPoolManager | None:
        """Lazy initialization of WorkerPoolManager.

        Returns None if not in a Kubernetes environment or if initialization fails.
        """
        if self._worker_pool_manager is None and config.ENTERPRISE_EDITION:
            try:
                from tracecat_ee.compute.manager import WorkerPoolManager

                self._worker_pool_manager = WorkerPoolManager(
                    in_cluster=config.TRACECAT__K8S_IN_CLUSTER
                )
            except Exception as e:
                self.logger.warning(
                    "Failed to initialize WorkerPoolManager",
                    error=str(e),
                )
        return self._worker_pool_manager

    async def list_organizations(self) -> Sequence[OrgRead]:
        """List all organizations."""
        stmt = select(Organization).order_by(Organization.created_at.desc())
        result = await self.session.execute(stmt)
        return OrgRead.list_adapter().validate_python(result.scalars().all())

    async def create_organization(self, params: OrgCreate) -> OrgRead:
        """Create a new organization."""
        org = Organization(
            id=uuid.uuid4(),
            name=params.name,
            slug=params.slug,
            tier=params.tier.value,
        )
        self.session.add(org)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(
                f"Organization with slug '{params.slug}' already exists"
            ) from e
        await self.session.refresh(org)

        # Provision worker pool for Enterprise tier on creation
        if params.tier == Tier.ENTERPRISE:
            await self._provision_worker_pool(str(org.id), params.tier)

        return OrgRead.model_validate(org)

    async def get_organization(self, org_id: uuid.UUID) -> OrgRead:
        """Get organization by ID."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        return OrgRead.model_validate(org)

    async def update_organization(
        self, org_id: uuid.UUID, params: OrgUpdate
    ) -> OrgRead:
        """Update organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        previous_tier = Tier(org.tier) if org.tier else Tier.STARTER

        for field, value in params.model_dump(exclude_unset=True).items():
            if field == "tier" and value is not None:
                setattr(org, field, value.value)
            else:
                setattr(org, field, value)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(f"Organization slug '{org.slug}' already exists") from e
        await self.session.refresh(org)

        # Handle tier change if tier was updated
        if params.tier is not None and params.tier != previous_tier:
            await self._handle_tier_change(str(org_id), previous_tier, params.tier)

        return OrgRead.model_validate(org)

    async def update_organization_tier(
        self, org_id: uuid.UUID, params: OrgUpdateTier
    ) -> TierChangeResult:
        """Update organization tier with worker pool management.

        This is the primary method for tier changes that handles worker pool
        provisioning/deprovisioning.
        """
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        previous_tier = Tier(org.tier) if org.tier else Tier.STARTER
        new_tier = params.tier

        if previous_tier == new_tier:
            return TierChangeResult(
                previous_tier=previous_tier,
                new_tier=new_tier,
                worker_pool_provisioned=False,
                worker_pool_deprovisioned=False,
            )

        # Update the tier in the database
        org.tier = new_tier.value
        await self.session.commit()
        await self.session.refresh(org)

        # Handle worker pool changes
        return await self._handle_tier_change(str(org_id), previous_tier, new_tier)

    async def delete_organization(self, org_id: uuid.UUID) -> None:
        """Delete organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        # Deprovision worker pool if Enterprise tier
        if Tier(org.tier) == Tier.ENTERPRISE:
            await self._deprovision_worker_pool(str(org_id))

        await self.session.delete(org)
        await self.session.commit()

    async def _handle_tier_change(
        self, org_id: str, previous_tier: Tier, new_tier: Tier
    ) -> TierChangeResult:
        """Handle worker pool provisioning/deprovisioning on tier change."""
        provisioned = False
        deprovisioned = False
        error = None

        try:
            # Enterprise -> Non-Enterprise: Deprovision dedicated pool
            if previous_tier == Tier.ENTERPRISE and new_tier != Tier.ENTERPRISE:
                await self._deprovision_worker_pool(org_id)
                deprovisioned = True
                self.logger.info(
                    "Deprovisioned worker pool for tier downgrade",
                    org_id=org_id,
                    previous_tier=previous_tier.value,
                    new_tier=new_tier.value,
                )

            # Non-Enterprise -> Enterprise: Provision dedicated pool
            elif previous_tier != Tier.ENTERPRISE and new_tier == Tier.ENTERPRISE:
                await self._provision_worker_pool(org_id, new_tier)
                provisioned = True
                self.logger.info(
                    "Provisioned worker pool for tier upgrade",
                    org_id=org_id,
                    previous_tier=previous_tier.value,
                    new_tier=new_tier.value,
                )

        except Exception as e:
            error = str(e)
            self.logger.error(
                "Failed to manage worker pool on tier change",
                org_id=org_id,
                previous_tier=previous_tier.value,
                new_tier=new_tier.value,
                error=error,
            )
            # Note: We don't rollback the tier change - the database state
            # is the source of truth. Worker pool can be reconciled later.

        return TierChangeResult(
            previous_tier=previous_tier,
            new_tier=new_tier,
            worker_pool_provisioned=provisioned,
            worker_pool_deprovisioned=deprovisioned,
            error=error,
        )

    async def _provision_worker_pool(self, org_id: str, tier: Tier) -> None:
        """Provision a worker pool for an organization."""
        if self.worker_pool_manager is None:
            self.logger.warning(
                "WorkerPoolManager not available, skipping provisioning",
                org_id=org_id,
            )
            return

        await self.worker_pool_manager.provision_worker_pool(
            org_id=org_id,
            tier=tier,
            namespace=config.TRACECAT__K8S_NAMESPACE,
        )

    async def _deprovision_worker_pool(self, org_id: str) -> None:
        """Deprovision a worker pool for an organization."""
        if self.worker_pool_manager is None:
            self.logger.warning(
                "WorkerPoolManager not available, skipping deprovisioning",
                org_id=org_id,
            )
            return

        await self.worker_pool_manager.deprovision_worker_pool(
            org_id=org_id,
            namespace=config.TRACECAT__K8S_NAMESPACE,
        )
