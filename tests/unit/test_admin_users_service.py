"""Tests for platform-level admin user service behavior."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped
from tracecat_ee.admin.users.schemas import AdminUserCreate
from tracecat_ee.admin.users.service import AdminUserService

from tracecat.auth.dex.service import DexLocalAuthProvisioningError
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import PlatformRole
from tracecat.db.models import Membership, OrganizationMembership, User

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
) -> None:
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="platform-user@example.com",
        password="this-is-a-strong-password",
        first_name="Platform",
        last_name="User",
        is_superuser=False,
    )

    with patch(
        "tracecat_ee.admin.users.service.get_dex_local_auth_service",
        return_value=None,
    ):
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

    with patch(
        "tracecat_ee.admin.users.service.get_dex_local_auth_service",
        return_value=None,
    ):
        created = await service.create_user(params)

    user_result = await session.execute(
        select(User).where(cast(Mapped[uuid.UUID], User.id) == created.id)
    )
    user = user_result.scalar_one()
    assert user.is_superuser is True


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

    with patch(
        "tracecat_ee.admin.users.service.get_dex_local_auth_service",
        return_value=None,
    ):
        await service.create_user(params)

    with (
        patch(
            "tracecat_ee.admin.users.service.get_dex_local_auth_service",
            return_value=None,
        ),
        pytest.raises(ValueError, match="already exists"),
    ):
        await service.create_user(params)


@pytest.mark.anyio
async def test_create_user_provisions_dex_local_auth_user(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="dex-user@example.com",
        password="this-is-a-strong-password",
    )
    dex_service = AsyncMock()

    with patch(
        "tracecat_ee.admin.users.service.get_dex_local_auth_service",
        return_value=dex_service,
    ):
        created = await service.create_user(params)

    dex_service.upsert_password.assert_awaited_once()
    kwargs = dex_service.upsert_password.await_args.kwargs
    assert kwargs["email"] == params.email
    assert kwargs["username"] == params.email
    assert kwargs["user_id"] == str(created.id)
    assert kwargs["password_hash"] != params.password
    assert kwargs["password_hash"].startswith("$2")


@pytest.mark.anyio
async def test_create_user_rolls_back_when_dex_provisioning_fails(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminUserService(session, role=platform_role)
    params = AdminUserCreate(
        email="dex-fail@example.com",
        password="this-is-a-strong-password",
    )
    dex_service = AsyncMock()
    dex_service.upsert_password.side_effect = DexLocalAuthProvisioningError("dex down")

    with (
        patch(
            "tracecat_ee.admin.users.service.get_dex_local_auth_service",
            return_value=dex_service,
        ),
        pytest.raises(DexLocalAuthProvisioningError, match="dex down"),
    ):
        await service.create_user(params)

    result = await session.execute(
        select(User).where(cast(Mapped[str], User.email) == params.email)
    )
    assert result.scalar_one_or_none() is None
