from __future__ import annotations

import asyncio
import uuid
from typing import cast

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from tracecat import config
from tracecat.auth.schemas import UserRole
from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
from tracecat.organization.management import (
    ensure_single_tenant_user_defaults,
    ensure_single_tenant_user_defaults_for_session,
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
async def test_single_tenant_defaults_for_session_noop_in_multi_tenant(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    result = await ensure_single_tenant_user_defaults_for_session(
        session=session,
        user_id=uuid.uuid4(),
        is_superuser=False,
    )

    assert result.organization_id is None
    assert result.changed is False


@pytest.mark.anyio
async def test_single_tenant_defaults_for_session_resolves_default_org(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    user = await _create_user(session)

    result = await ensure_single_tenant_user_defaults_for_session(
        session=session,
        user_id=user.id,
        is_superuser=False,
    )
    await session.flush()

    assert result.organization_id is not None
    assert result.changed is True
    membership = await session.get(
        OrganizationMembership,
        {"user_id": user.id, "organization_id": result.organization_id},
    )
    assert membership is not None


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
async def test_single_tenant_defaults_handle_concurrent_repairs() -> None:
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    org_slug = f"single-tenant-concurrent-{uuid.uuid4().hex[:8]}"

    async with get_async_session_bypass_rls_context_manager() as setup_session:
        setup_session.add(
            Organization(
                id=org_id,
                name="Single Tenant Concurrent Test Org",
                slug=org_slug,
                is_active=True,
            )
        )
        setup_session.add(
            User(
                id=user_id,
                email=f"concurrent-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password="hashed",
                role=UserRole.BASIC,
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        await setup_session.flush()
        await seed_system_roles_for_org(setup_session, org_id)
        await setup_session.commit()

    async def repair_defaults() -> None:
        async with get_async_session_bypass_rls_context_manager() as repair_session:
            await ensure_single_tenant_user_defaults_in_session(
                session=repair_session,
                user_id=user_id,
                organization_id=org_id,
                is_superuser=False,
            )
            await repair_session.commit()

    try:
        await asyncio.gather(repair_defaults(), repair_defaults())

        async with get_async_session_bypass_rls_context_manager() as verify_session:
            membership_count = await verify_session.scalar(
                select(func.count())
                .select_from(OrganizationMembership)
                .where(
                    OrganizationMembership.user_id == user_id,
                    OrganizationMembership.organization_id == org_id,
                )
            )
            assignment_count = await verify_session.scalar(
                select(func.count())
                .select_from(UserRoleAssignment)
                .where(
                    UserRoleAssignment.user_id == user_id,
                    UserRoleAssignment.organization_id == org_id,
                    UserRoleAssignment.workspace_id.is_(None),
                )
            )

        assert membership_count == 1
        assert assignment_count == 1
    finally:
        async with get_async_session_bypass_rls_context_manager() as cleanup_session:
            await cleanup_session.execute(
                delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id)
            )
            await cleanup_session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == user_id
                )
            )
            await cleanup_session.execute(
                delete(User).where(cast(Mapped[uuid.UUID], User.id) == user_id)
            )
            await cleanup_session.execute(
                delete(Organization).where(
                    cast(Mapped[uuid.UUID], Organization.id) == org_id
                )
            )
            await cleanup_session.commit()


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
