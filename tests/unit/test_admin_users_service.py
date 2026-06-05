"""Tests for platform-level admin user service behavior."""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped
from tracecat_ee.admin.users.schemas import AdminUserCreate
from tracecat_ee.admin.users.service import AdminUserService

from tracecat import config
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    AccessToken,
    Membership,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.mark.anyio
async def test_create_user_creates_platform_user_without_memberships(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="platform-user@example.com",
        password="this-is-a-strong-password",
        first_name="Platform",
        last_name="User",
        is_superuser=False,
    )

    created = await service.create_user(params)

    user_result = await session.execute(
        select(User).where(cast(Mapped[uuid.UUID], User.id) == created.id)
    )
    user = user_result.scalar_one()
    assert user.email == params.email
    assert user.first_name == params.first_name
    assert user.last_name == params.last_name
    assert user.role == UserRole.BASIC
    assert user.is_verified is True
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.hashed_password != params.password

    org_membership_result = await session.execute(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.user_id == created.id)
    )
    org_membership_count = org_membership_result.scalar_one()
    assert org_membership_count == 0

    workspace_membership_result = await session.execute(
        select(func.count())
        .select_from(Membership)
        .where(Membership.user_id == created.id)
    )
    workspace_membership_count = workspace_membership_result.scalar_one()
    assert workspace_membership_count == 0


@pytest.mark.anyio
async def test_create_user_respects_superuser_flag(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="platform-superuser@example.com",
        password="this-is-a-strong-password",
        is_superuser=True,
    )

    created = await service.create_user(params)

    user_result = await session.execute(
        select(User).where(cast(Mapped[uuid.UUID], User.id) == created.id)
    )
    user = user_result.scalar_one()
    assert user.is_superuser is True


@pytest.mark.anyio
async def test_create_user_provisions_default_org_in_single_tenant(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="single-tenant-user@example.com",
        password="this-is-a-strong-password",
        is_superuser=False,
    )

    created = await service.create_user(params)

    membership_count = await session.scalar(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.user_id == created.id)
    )
    role_slug = await session.scalar(
        select(DBRole.slug)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == created.id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    workspace_membership_count = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.user_id == created.id)
    )

    assert membership_count == 1
    assert role_slug == "organization-member"
    assert workspace_membership_count == 0


@pytest.mark.anyio
async def test_promote_superuser_provisions_owner_in_single_tenant(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    service = AdminUserService(session, role=platform_role)
    created = await service.create_user(
        AdminUserCreate(
            email="promoted-single-tenant-user@example.com",
            password="this-is-a-strong-password",
            is_superuser=False,
        )
    )

    await service.promote_superuser(created.id)

    role_slug = await session.scalar(
        select(DBRole.slug)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == created.id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    assert role_slug == "organization-owner"


@pytest.mark.anyio
async def test_create_user_rejects_duplicate_email(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="duplicate@example.com",
        password="this-is-a-strong-password",
    )

    await service.create_user(params)

    with pytest.raises(ValueError, match="already exists"):
        await service.create_user(params)


@pytest.mark.anyio
async def test_delete_user_clears_sessions_and_memberships(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)
    org = Organization(
        id=uuid.uuid4(),
        name="Delete User Org",
        slug=f"delete-user-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    user = User(
        id=uuid.uuid4(),
        email="delete-me@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add_all([org, user])
    await session.flush()
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Delete User Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.flush()
    token = AccessToken(token=f"token-{uuid.uuid4().hex}", user_id=user.id)
    session.add_all(
        [
            token,
            OrganizationMembership(user_id=user.id, organization_id=org.id),
            Membership(user_id=user.id, workspace_id=workspace.id),
        ]
    )
    await session.commit()

    token_id = token.id
    user_id = user.id

    await service.delete_user(user_id, current_user_id=platform_role.user_id)

    assert (
        await session.scalar(
            select(User).where(cast(Mapped[uuid.UUID], User.id) == user_id)
        )
        is None
    )
    assert (
        await session.scalar(select(AccessToken).where(AccessToken.id == token_id))
        is None
    )
    assert (
        await session.scalar(select(Membership).where(Membership.user_id == user_id))
        is None
    )
    assert (
        await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id
            )
        )
        is None
    )


@pytest.mark.anyio
async def test_delete_user_rejects_self(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)

    with pytest.raises(ValueError, match="Cannot delete yourself"):
        await service.delete_user(
            platform_role.user_id, current_user_id=platform_role.user_id
        )


@pytest.mark.anyio
async def test_delete_user_rejects_last_superuser(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)
    await session.execute(update(User).values(is_superuser=False))
    user = User(
        id=uuid.uuid4(),
        email="last-superuser@example.com",
        hashed_password="hashed",
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    session.add(user)
    await session.commit()

    with pytest.raises(ValueError, match="Cannot delete the last superuser"):
        await service.delete_user(user.id, current_user_id=platform_role.user_id)
