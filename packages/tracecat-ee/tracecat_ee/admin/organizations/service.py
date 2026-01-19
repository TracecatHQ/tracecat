"""Organization management service for admin control plane."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.db.models import Organization
from tracecat.service import BaseService
from tracecat_ee.admin.organizations.schemas import OrgCreate, OrgRead, OrgUpdate


class AdminOrgService(BaseService):
    """Platform-level organization management."""

    service_name = "admin_org"

    async def _count_orgs(self) -> int:
        """Count existing organizations."""
        stmt = select(func.count()).select_from(Organization)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def list_organizations(self) -> Sequence[OrgRead]:
        """List all organizations."""
        stmt = select(Organization).order_by(Organization.created_at.desc())
        result = await self.session.execute(stmt)
        return OrgRead.list_adapter().validate_python(result.scalars().all())

    async def create_organization(self, params: OrgCreate) -> OrgRead:
        """Create a new organization.

        In CE mode, only a single organization is allowed.
        In EE mode, multiple organizations are supported.
        """
        # Enforce single-org limit in CE mode
        if config.TRACECAT__DEPLOYMENT_MODE == "CE":
            existing_count = await self._count_orgs()
            if existing_count >= 1:
                raise ValueError(
                    "CE supports single organization only. "
                    "Set TRACECAT__DEPLOYMENT_MODE=EE for multi-org."
                )

        org = Organization(
            id=uuid.uuid4(),
            name=params.name,
            slug=params.slug,
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

        for field, value in params.model_dump(exclude_unset=True).items():
            setattr(org, field, value)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise ValueError(f"Organization slug '{org.slug}' already exists") from e
        await self.session.refresh(org)
        return OrgRead.model_validate(org)

    async def delete_organization(self, org_id: uuid.UUID) -> None:
        """Delete organization."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if not org:
            raise ValueError(f"Organization {org_id} not found")

        await self.session.delete(org)
        await self.session.commit()
