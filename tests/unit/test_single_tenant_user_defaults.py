from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.schemas import UserRole
from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
from tracecat.organization.management import (
    ensure_single_tenant_user_defaults,
    ensure_single_tenant_user_defaults_in_session,
)

pytestmark = pytest.mark.usefixtures("db")


async def _create_org_with_roles(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Single Tenant Test Org",
        slug=f"single-tenant-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.flush()
    await seed_system_roles_for_org(session, org.id)
    return org


async def _create_user(session: AsyncSession, *, is_superuser: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.ADMIN if is_superuser else UserRole.BASIC,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _get_org_role_assignment_slug(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> str:
    result = await session.execute(
        select(DBRole.slug)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    role_slug = result.scalar_one()
    assert role_slug is not None
    return role_slug


@pytest.mark.anyio
async def test_single_tenant_defaults_noop_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    organization_id = await ensure_single_tenant_user_defaults(
        user_id=uuid.uuid4(),
        is_superuser=False,
    )

    assert organization_id is None


@pytest.mark.anyio
async def test_single_tenant_defaults_assign_member_role(
    session: AsyncSession,
) -> None:
    org = await _create_org_with_roles(session)
    user = await _create_user(session)

    await ensure_single_tenant_user_defaults_in_session(
        session=session,
        user_id=user.id,
        organization_id=org.id,
        is_superuser=False,
    )
    await session.flush()

    membership = await session.get(
        OrganizationMembership,
        {"user_id": user.id, "organization_id": org.id},
    )
    assert membership is not None
    assert (
        await _get_org_role_assignment_slug(
            session, user_id=user.id, organization_id=org.id
        )
        == "organization-member"
    )


@pytest.mark.anyio
async def test_single_tenant_defaults_assign_owner_for_superuser(
    session: AsyncSession,
) -> None:
    org = await _create_org_with_roles(session)
    user = await _create_user(session, is_superuser=True)

    await ensure_single_tenant_user_defaults_in_session(
        session=session,
        user_id=user.id,
        organization_id=org.id,
        is_superuser=True,
    )
    await session.flush()

    assert (
        await _get_org_role_assignment_slug(
            session, user_id=user.id, organization_id=org.id
        )
        == "organization-owner"
    )


@pytest.mark.anyio
async def test_single_tenant_defaults_are_idempotent(
    session: AsyncSession,
) -> None:
    org = await _create_org_with_roles(session)
    user = await _create_user(session)

    for _ in range(2):
        await ensure_single_tenant_user_defaults_in_session(
            session=session,
            user_id=user.id,
            organization_id=org.id,
            is_superuser=False,
        )
    await session.flush()

    membership_count = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == org.id,
        )
    )
    assignment_result = await session.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user.id,
            UserRoleAssignment.organization_id == org.id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    assert membership_count is not None
    assert len(assignment_result.scalars().all()) == 1


@pytest.mark.anyio
async def test_single_tenant_defaults_do_not_downgrade_existing_role(
    session: AsyncSession,
) -> None:
    org = await _create_org_with_roles(session)
    user = await _create_user(session)
    owner_role = (
        await session.execute(
            select(DBRole).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "organization-owner",
            )
        )
    ).scalar_one()
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=None,
            role_id=owner_role.id,
        )
    )
    await session.flush()

    await ensure_single_tenant_user_defaults_in_session(
        session=session,
        user_id=user.id,
        organization_id=org.id,
        is_superuser=False,
    )
    await session.flush()

    assert (
        await _get_org_role_assignment_slug(
            session, user_id=user.id, organization_id=org.id
        )
        == "organization-owner"
    )


@pytest.mark.anyio
async def test_single_tenant_defaults_upgrade_superuser_to_owner(
    session: AsyncSession,
) -> None:
    org = await _create_org_with_roles(session)
    user = await _create_user(session, is_superuser=True)
    member_role = (
        await session.execute(
            select(DBRole).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "organization-member",
            )
        )
    ).scalar_one()
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=None,
            role_id=member_role.id,
        )
    )
    await session.flush()

    await ensure_single_tenant_user_defaults_in_session(
        session=session,
        user_id=user.id,
        organization_id=org.id,
        is_superuser=True,
    )
    await session.flush()

    assert (
        await _get_org_role_assignment_slug(
            session, user_id=user.id, organization_id=org.id
        )
        == "organization-owner"
    )
