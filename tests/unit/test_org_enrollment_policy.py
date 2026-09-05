"""Organization enrollment policy for single-tenant deployments.

Membership is granted by provisioning or invitation acceptance. Self-service
paths repair existing members without admitting new ones.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.membership import grant_org_membership
from tracecat.auth.schemas import UserRole
from tracecat.auth.users import UserManager
from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.organization.management import (
    ensure_single_tenant_user_defaults_for_session,
)

pytestmark = pytest.mark.usefixtures("db")


async def _create_org_with_roles(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Enrollment Policy Test Org",
        slug=f"enrollment-policy-{uuid.uuid4().hex[:8]}",
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


async def _org_role_assignment_count(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> int:
    result = await session.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    return len(result.all())


async def _assert_not_enrolled(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> None:
    membership = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    assert membership is None, "account was enrolled into the organization"
    assert (
        await _org_role_assignment_count(
            session, user_id=user_id, organization_id=organization_id
        )
        == 0
    ), "account received an org-wide role assignment"


@pytest.mark.anyio
async def test_registration_does_not_enroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``on_after_register`` leaves enrollment to provisioning or invitation."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_superuser = False

    defaults = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "tracecat.auth.users.ensure_single_tenant_user_defaults", defaults
    )
    monkeypatch.setattr(
        "tracecat.auth.users.config.TRACECAT__AUTH_SUPERADMIN_EMAIL", None
    )

    manager = UserManager.__new__(UserManager)
    manager.logger = MagicMock()
    manager._pending_invitation_token = None
    monkeypatch.setattr(manager, "_emit_user_create_audit", AsyncMock())

    await manager.on_after_register(user)

    assert defaults.await_args is not None
    assert defaults.await_args.kwargs.get("allow_new_members", False) is False


@pytest.mark.anyio
async def test_self_service_path_does_not_enroll(
    session: AsyncSession,
) -> None:
    """``allow_new_members=False`` blocks admission rather than only advising it."""
    org = await _create_org_with_roles(session)
    user = await _create_user(session)

    result = await ensure_single_tenant_user_defaults_for_session(
        session=session,
        user_id=user.id,
        is_superuser=False,
        organization_id=org.id,
        allow_new_members=False,
    )
    await session.flush()

    assert result.organization_id is None
    assert not result.changed
    await _assert_not_enrolled(session, user_id=user.id, organization_id=org.id)


@pytest.mark.anyio
async def test_admission_is_denied_by_default(
    session: AsyncSession,
) -> None:
    """Omitting ``allow_new_members`` does not admit the account.

    Callers that inherit the default never pass the argument, so the default
    itself needs direct coverage.
    """
    org = await _create_org_with_roles(session)
    user = await _create_user(session)

    result = await ensure_single_tenant_user_defaults_for_session(
        session=session,
        user_id=user.id,
        is_superuser=False,
        organization_id=org.id,
    )
    await session.flush()

    assert result.organization_id is None
    await _assert_not_enrolled(session, user_id=user.id, organization_id=org.id)


@pytest.mark.anyio
async def test_existing_member_is_still_repaired(
    session: AsyncSession,
) -> None:
    """An account with a membership row keeps its role repaired."""
    org = await _create_org_with_roles(session)
    user = await _create_user(session)

    await grant_org_membership(session, user_id=user.id, organization_id=org.id)

    result = await ensure_single_tenant_user_defaults_for_session(
        session=session,
        user_id=user.id,
        is_superuser=False,
        organization_id=org.id,
        allow_new_members=False,
    )
    await session.flush()

    assert result.organization_id == org.id
    assert (
        await _org_role_assignment_count(
            session, user_id=user.id, organization_id=org.id
        )
        == 1
    )


@pytest.mark.anyio
async def test_superuser_bootstrap_still_enrolls(
    session: AsyncSession,
) -> None:
    """Superuser bootstrap provisions membership regardless of the flag."""
    org = await _create_org_with_roles(session)
    user = await _create_user(session, is_superuser=True)

    result = await ensure_single_tenant_user_defaults_for_session(
        session=session,
        user_id=user.id,
        is_superuser=True,
        organization_id=org.id,
        allow_new_members=False,
    )
    await session.flush()

    assert result.organization_id == org.id
    membership = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
    ).scalar_one_or_none()
    assert membership is not None
