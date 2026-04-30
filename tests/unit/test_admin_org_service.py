"""Tests for the platform-level ``AdminOrgService.update_organization``.

These exercise the dynamic ``setattr`` field-propagation loop, which is the
mechanism that surfaces ``disable_github_workflow_pulls`` (and any future
columns added to the Organization model) on the EE admin write path.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.organizations.schemas import OrgUpdate
from tracecat_ee.admin.organizations.service import AdminOrgService

from tracecat.auth.types import PlatformRole
from tracecat.db.models import Organization

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
async def organization(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Acme Org",
        slug=f"acme-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.mark.anyio
async def test_new_organization_defaults_disable_pulls_to_false(
    organization: Organization,
) -> None:
    """The DB default keeps existing pull behavior unchanged."""
    assert organization.disable_github_workflow_pulls is False


@pytest.mark.anyio
async def test_update_organization_enables_disable_github_workflow_pulls(
    session: AsyncSession,
    platform_role: PlatformRole,
    organization: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)

    updated = await service.update_organization(
        organization.id,
        OrgUpdate(name=None, slug=None, disable_github_workflow_pulls=True),
    )

    assert updated.disable_github_workflow_pulls is True
    await session.refresh(organization)
    assert organization.disable_github_workflow_pulls is True


@pytest.mark.anyio
async def test_update_organization_disables_disable_github_workflow_pulls(
    session: AsyncSession,
    platform_role: PlatformRole,
    organization: Organization,
) -> None:
    """Re-enabling pulls clears the flag without touching other fields."""
    organization.disable_github_workflow_pulls = True
    await session.commit()

    service = AdminOrgService(session, role=platform_role)
    updated = await service.update_organization(
        organization.id,
        OrgUpdate(name=None, slug=None, disable_github_workflow_pulls=False),
    )

    assert updated.disable_github_workflow_pulls is False
    assert updated.is_active is True
    assert updated.name == organization.name


@pytest.mark.anyio
async def test_update_organization_only_touches_set_fields(
    session: AsyncSession,
    platform_role: PlatformRole,
    organization: Organization,
) -> None:
    """An ``OrgUpdate`` without the new field leaves the flag untouched."""
    organization.disable_github_workflow_pulls = True
    await session.commit()

    service = AdminOrgService(session, role=platform_role)
    updated = await service.update_organization(
        organization.id,
        OrgUpdate(name="Renamed Org", slug=None),
    )

    assert updated.name == "Renamed Org"
    assert updated.disable_github_workflow_pulls is True
