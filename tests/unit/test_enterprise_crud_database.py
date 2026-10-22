"""PostgreSQL regressions for enterprise CRUD constraints and serialization."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tracecat_ee.admin.organizations.schemas import (
    OrgDomainCreate,
    OrgDomainUpdate,
    OrgUpdate,
)
from tracecat_ee.admin.organizations.service import AdminOrgService
from tracecat_ee.admin.tiers.service import AdminTierService

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import PlatformRole
from tracecat.db.models import Organization, OrganizationDomain, OrganizationTier, Tier
from tracecat.exceptions import TracecatConflictError
from tracecat.tiers.schemas import OrganizationTierUpdate

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(type="user", user_id=uuid4(), service_id="tracecat-api")


@pytest.fixture
async def committed_org() -> AsyncIterator[
    tuple[UUID, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid4()
    tier_id = uuid4()
    async with factory() as session:
        session.add_all(
            [
                Organization(
                    id=org_id, name="Synthetic", slug=f"synthetic-{org_id.hex}"
                ),
                Tier(
                    id=tier_id,
                    display_name="Synthetic",
                    entitlements={},
                    is_default=True,
                ),
            ]
        )
        await session.commit()
    try:
        yield org_id, factory
    finally:
        async with factory() as session:
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.execute(delete(Tier).where(Tier.id == tier_id))
            await session.commit()
        await engine.dispose()


async def test_live_constraint_names(session: AsyncSession) -> None:
    names = set(
        (
            await session.scalars(
                text(
                    "SELECT conname FROM pg_constraint UNION SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
                )
            )
        ).all()
    )
    assert {
        "uq_scope_organization_id_name",
        "uq_group_organization_id_name",
        "pk_group_member",
        "uq_group_role_assignment_group_id_workspace_id",
        "ix_group_role_assignment_group_org_unique",
        "uq_user_role_assignment_user_id_workspace_id",
        "ix_user_role_assignment_user_org_unique",
        "uq_organization_secret_store_organization_id_name",
        "uq_secret_name_environment_workspace_id",
        "fk_secret_store_id_organization_secret_store",
        "fk_secret_store_authorization",
        "ix_invitation_org_email_pending_unique",
        "ix_organization_slug",
        "ix_user_email",
        "ix_org_domain_normalized_domain_active_unique",
        "ix_org_domain_org_primary_active_unique",
    } <= names


async def test_concurrent_first_org_tier_updates_share_assignment(
    committed_org: tuple[UUID, async_sessionmaker[AsyncSession]],
    platform_role: PlatformRole,
) -> None:
    org_id, factory = committed_org
    barrier = asyncio.Barrier(4)

    async def update_limit(limit: int) -> UUID:
        async with factory() as session:
            await barrier.wait()
            result = await AdminTierService(session, platform_role).update_org_tier(
                org_id, OrganizationTierUpdate(max_concurrent_actions=limit)
            )
            return result.id

    ids = await asyncio.wait_for(
        asyncio.gather(*(update_limit(i) for i in range(1, 5))), timeout=20
    )
    assert len(set(ids)) == 1
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OrganizationTier)
                .where(OrganizationTier.organization_id == org_id)
            )
            == 1
        )


async def test_concurrent_first_domains_keep_one_primary(
    committed_org: tuple[UUID, async_sessionmaker[AsyncSession]],
    platform_role: PlatformRole,
) -> None:
    org_id, factory = committed_org
    barrier = asyncio.Barrier(4)

    async def create_domain(index: int) -> None:
        async with factory() as session:
            await barrier.wait()
            await AdminOrgService(session, platform_role).create_org_domain(
                org_id,
                OrgDomainCreate(domain=f"synthetic-{index}-{org_id.hex}.example.com"),
            )

    await asyncio.wait_for(
        asyncio.gather(*(create_domain(i) for i in range(4))), timeout=20
    )
    async with factory() as session:
        domains = list(
            (
                await session.scalars(
                    select(OrganizationDomain).where(
                        OrganizationDomain.organization_id == org_id
                    )
                )
            ).all()
        )
        assert len(domains) == 4
        assert sum(domain.is_primary for domain in domains) == 1
        target = next(domain for domain in domains if not domain.is_primary)
        promoted = await AdminOrgService(session, platform_role).update_org_domain(
            org_id, target.id, OrgDomainUpdate(is_primary=True)
        )
        assert promoted.is_primary
        session.expire_all()
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OrganizationDomain)
                .where(
                    OrganizationDomain.organization_id == org_id,
                    OrganizationDomain.is_primary.is_(True),
                )
            )
            == 1
        )


async def test_org_slug_conflict_rolls_back_and_session_recovers(
    session: AsyncSession, platform_role: PlatformRole
) -> None:
    first = Organization(name="Synthetic first", slug=f"synthetic-{uuid4().hex}")
    second = Organization(name="Synthetic second", slug=f"synthetic-{uuid4().hex}")
    session.add_all([first, second])
    await session.commit()
    first_slug, second_id = first.slug, second.id
    service = AdminOrgService(session, platform_role)
    with pytest.raises(TracecatConflictError):
        await service.update_organization(
            second_id, OrgUpdate.model_validate({"slug": first_slug})
        )
    updated = await service.update_organization(
        second_id, OrgUpdate.model_validate({"name": "Synthetic recovered"})
    )
    assert updated.name == "Synthetic recovered"
