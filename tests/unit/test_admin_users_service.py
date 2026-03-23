"""Tests for platform-level admin user service behavior."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped
from tracecat_ee.admin.users.schemas import AdminUserCreate
from tracecat_ee.admin.users.service import AdminUserService

from tracecat.auth.dex.service import DexLocalAuthProvisioningError
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import PlatformRole
from tracecat.auth.users import UserManager
from tracecat.db.models import Membership, OrganizationMembership, User

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


def build_admin_service(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> AdminUserService:
    return AdminUserService(session, role=platform_role)


def patch_dex_service(
    monkeypatch: pytest.MonkeyPatch,
    service: object,
) -> None:
    monkeypatch.setattr(
        "tracecat.auth.users.get_dex_local_auth_service",
        lambda: service,
    )


def make_admin_user_create(
    *,
    email: str,
    password: str = "this-is-a-strong-password",
    is_superuser: bool = False,
    first_name: str | None = None,
    last_name: str | None = None,
) -> AdminUserCreate:
    return AdminUserCreate(
        email=email,
        password=password,
        is_superuser=is_superuser,
        first_name=first_name,
        last_name=last_name,
    )


@pytest.mark.anyio
async def test_create_user_creates_platform_user_without_memberships(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(
        email="platform-user@example.com",
        first_name="Platform",
        last_name="User",
    )

    patch_dex_service(monkeypatch, None)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(
        email="platform-superuser@example.com",
        is_superuser=True,
    )

    patch_dex_service(monkeypatch, None)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(email="duplicate@example.com")

    patch_dex_service(monkeypatch, None)
    await service.create_user(params)

    with pytest.raises(ValueError, match="already exists"):
        await service.create_user(params)


@pytest.mark.anyio
async def test_create_user_provisions_dex_local_auth_user(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(email="dex-user@example.com")
    dex_service = AsyncMock()

    patch_dex_service(monkeypatch, dex_service)
    created = await service.create_user(params)

    dex_service.upsert_password.assert_awaited_once()
    kwargs = dex_service.upsert_password.await_args.kwargs
    assert kwargs["email"] == params.email
    assert kwargs["username"] == params.email
    assert kwargs["user_id"] == str(created.id)
    assert kwargs["password_hash"] != params.password
    assert kwargs["password_hash"].startswith("$2")


@pytest.mark.anyio
async def test_create_user_skips_dex_local_auth_when_policy_blocks_password_login(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(email="dex-blocked@example.com")
    dex_service = AsyncMock()

    patch_dex_service(monkeypatch, dex_service)
    monkeypatch.setattr(
        UserManager,
        "is_local_password_login_allowed",
        AsyncMock(return_value=False),
    )

    await service.create_user(params)

    dex_service.upsert_password.assert_not_awaited()
    dex_service.delete_password.assert_awaited_once_with(params.email)


@pytest.mark.anyio
async def test_create_user_rolls_back_when_dex_provisioning_fails(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(email="dex-fail@example.com")
    dex_service = AsyncMock()
    dex_service.upsert_password.side_effect = DexLocalAuthProvisioningError("dex down")

    patch_dex_service(monkeypatch, dex_service)
    with pytest.raises(DexLocalAuthProvisioningError, match="dex down"):
        await service.create_user(params)

    result = await session.execute(
        select(User).where(cast(Mapped[str], User.email) == params.email)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.anyio
async def test_create_user_cleans_up_dex_local_auth_when_commit_fails(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_service(session, platform_role)
    params = make_admin_user_create(email="dex-rollback@example.com")
    dex_service = AsyncMock()
    original_commit = session.commit

    async def _failing_commit() -> None:
        session.commit = original_commit
        raise RuntimeError("db commit failed")

    patch_dex_service(monkeypatch, dex_service)
    monkeypatch.setattr(session, "commit", _failing_commit)

    with pytest.raises(RuntimeError, match="db commit failed"):
        await service.create_user(params)

    dex_service.upsert_password.assert_awaited_once()
    dex_service.delete_password.assert_awaited_once_with(params.email)
    result = await session.execute(
        select(User).where(cast(Mapped[str], User.email) == params.email)
    )
    assert result.scalar_one_or_none() is None
