"""Tests for superuser organization domain assignment service behavior."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.organizations.schemas import OrgDomainCreate, OrgDomainUpdate
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
async def org_a(session: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Org A",
        slug=f"org-a-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.commit()
    return organization


@pytest.fixture
async def org_b(session: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Org B",
        slug=f"org-b-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.commit()
    return organization


@pytest.mark.anyio
async def test_first_active_domain_is_primary_by_default(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)

    created = await service.create_org_domain(
        org_a.id, OrgDomainCreate(domain="  Acme.com.  ")
    )

    assert created.domain == "acme.com"
    assert created.normalized_domain == "acme.com"
    assert created.is_primary is True
    assert created.is_active is True


@pytest.mark.anyio
async def test_duplicate_active_domain_across_orgs_is_rejected(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
    org_b: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)

    await service.create_org_domain(org_a.id, OrgDomainCreate(domain="example.com"))

    with pytest.raises(ValueError, match="already assigned"):
        await service.create_org_domain(org_b.id, OrgDomainCreate(domain="EXAMPLE.COM"))


@pytest.mark.anyio
async def test_deactivating_primary_promotes_next_active_domain(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)
    first = await service.create_org_domain(
        org_a.id, OrgDomainCreate(domain="a.example")
    )
    second = await service.create_org_domain(
        org_a.id, OrgDomainCreate(domain="b.example")
    )

    updated_first = await service.update_org_domain(
        org_a.id, first.id, OrgDomainUpdate(is_active=False)
    )
    domains = await service.list_org_domains(org_a.id)
    updated_second = next(domain for domain in domains if domain.id == second.id)

    assert updated_first.is_active is False
    assert updated_first.is_primary is False
    assert updated_second.is_primary is True


@pytest.mark.anyio
async def test_deleting_primary_promotes_next_active_domain(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)
    first = await service.create_org_domain(
        org_a.id, OrgDomainCreate(domain="a.example")
    )
    second = await service.create_org_domain(
        org_a.id, OrgDomainCreate(domain="b.example")
    )

    await service.delete_org_domain(org_a.id, first.id)
    domains = await service.list_org_domains(org_a.id)

    assert len(domains) == 1
    assert domains[0].id == second.id
    assert domains[0].is_primary is True


@pytest.mark.anyio
async def test_primary_domain_cannot_be_set_inactive_in_same_update(
    session: AsyncSession,
    platform_role: PlatformRole,
    org_a: Organization,
) -> None:
    service = AdminOrgService(session, role=platform_role)
    domain = await service.create_org_domain(
        org_a.id, OrgDomainCreate(domain="a.example")
    )

    with pytest.raises(ValueError, match="Primary domain must be active"):
        await service.update_org_domain(
            org_a.id,
            domain.id,
            OrgDomainUpdate(is_primary=True, is_active=False),
        )
