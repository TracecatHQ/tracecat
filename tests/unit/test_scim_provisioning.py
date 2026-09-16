"""Unit tests for SCIM user provisioning."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.provisioning import ScimProvisioningService

from tracecat.auth.types import Role
from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.models import (
    ExternalUser,
    Invitation,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
from tracecat.invitations.enums import InvitationStatus


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def auth_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the auth bulkhead session at the test session.

    Email validation counts users on its own connection, which cannot see this
    test's uncommitted savepoint and would take the first-user branch.
    """

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        "tracecat.auth.users.get_async_session_auth_context_manager",
        _session_cm,
    )


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="SCIM Org", slug=f"scim-org-{org_id.hex[:8]}")
    session.add(org)
    await session.flush()
    # An existing user keeps provisioning off the first-user superadmin path.
    session.add(
        User(
            id=uuid.uuid4(),
            email=f"seed-{uuid.uuid4().hex[:8]}@tracecat.com",
            hashed_password="x",
        )
    )
    await session.flush()
    return org


@pytest.fixture
def role(org: Organization) -> Role:
    return Role(
        type="scim",
        organization_id=org.id,
        scim_connection_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"org:member:remove"}),
    )


@pytest.fixture
def service(session: AsyncSession, role: Role) -> ScimProvisioningService:
    return ScimProvisioningService(session, role=role)


async def _is_member(
    session: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> bool:
    stmt = select(OrganizationMembership).where(
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.organization_id == organization_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


@pytest.mark.anyio
async def test_provision_creates_user_and_admits_them(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """A new email becomes an account that is present in the organization."""
    email = f"new-{uuid.uuid4().hex[:8]}@tracecat.com"

    provisioned = await service.provision_user(external_id="idp-1", email=email)

    assert provisioned.created is True
    assert provisioned.user.email == email
    assert await _is_member(
        session, user_id=provisioned.user.id, organization_id=org.id
    )

    linkage = (
        await session.execute(
            select(ExternalUser.external_id).where(
                ExternalUser.organization_id == org.id,
                ExternalUser.user_id == provisioned.user.id,
            )
        )
    ).scalar_one()
    assert linkage == "idp-1"


@pytest.mark.anyio
async def test_provision_links_existing_account_by_email(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """An email that already has an account is linked, never duplicated."""
    email = f"existing-{uuid.uuid4().hex[:8]}@tracecat.com"
    existing = User(id=uuid.uuid4(), email=email, hashed_password="x")
    session.add(existing)
    await session.flush()

    provisioned = await service.provision_user(external_id="idp-2", email=email)

    assert provisioned.created is False
    assert provisioned.user.id == existing.id
    count = await session.scalar(
        select(func.count()).select_from(User).where(func.lower(User.email) == email)
    )
    assert count == 1


@pytest.mark.anyio
async def test_provision_links_case_insensitively(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """The provider's casing does not create a second account."""
    email = f"casing-{uuid.uuid4().hex[:8]}@tracecat.com"
    existing = User(id=uuid.uuid4(), email=email, hashed_password="x")
    session.add(existing)
    await session.flush()

    provisioned = await service.provision_user(external_id="idp-3", email=email.upper())

    assert provisioned.created is False
    assert provisioned.user.id == existing.id


@pytest.mark.anyio
async def test_repeated_provision_is_idempotent(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """A retried POST links rather than conflicting, and leaves one linkage."""
    email = f"retry-{uuid.uuid4().hex[:8]}@tracecat.com"

    first = await service.provision_user(external_id="idp-4", email=email)
    second = await service.provision_user(external_id="idp-4", email=email)

    assert first.user.id == second.user.id
    assert second.created is False
    linkages = await session.scalar(
        select(func.count())
        .select_from(ExternalUser)
        .where(
            ExternalUser.organization_id == org.id,
            ExternalUser.user_id == first.user.id,
        )
    )
    assert linkages == 1


@pytest.mark.anyio
async def test_inactive_user_is_linked_but_not_admitted(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """A user pushed inactive gets no role assignment, so no org presence."""
    email = f"inactive-{uuid.uuid4().hex[:8]}@tracecat.com"

    provisioned = await service.provision_user(
        external_id="idp-5", email=email, active=False
    )

    assert not await _is_member(
        session, user_id=provisioned.user.id, organization_id=org.id
    )


@pytest.mark.anyio
async def test_pending_invitation_is_revoked_on_provisioning(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """A live invite carries its own role, so provisioning revokes it.

    Accepting it afterwards would grant whatever the inviter chose, which can
    outrank what SCIM granted.
    """
    email = f"invited-{uuid.uuid4().hex[:8]}@tracecat.com"
    await seed_system_roles_for_org(session, org.id)
    admin_role_id = (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "organization-admin",
            )
        )
    ).scalar_one()
    invitation = Invitation(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        status=InvitationStatus.PENDING,
        role_id=admin_role_id,
        token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    session.add(invitation)
    await session.flush()

    await service.provision_user(external_id="idp-6", email=email)

    await session.refresh(invitation)
    assert invitation.status == InvitationStatus.REVOKED


@pytest.mark.anyio
async def test_provision_grants_only_the_member_role(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """Provisioning admits at organization-member, never higher."""
    email = f"member-{uuid.uuid4().hex[:8]}@tracecat.com"

    provisioned = await service.provision_user(external_id="idp-7", email=email)

    slug = (
        await session.execute(
            select(DBRole.slug)
            .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
            .where(
                UserRoleAssignment.user_id == provisioned.user.id,
                UserRoleAssignment.organization_id == org.id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
    ).scalar_one()
    assert slug == "organization-member"


@pytest.mark.anyio
async def test_existing_higher_role_is_not_downgraded(
    session: AsyncSession, org: Organization, service: ScimProvisioningService
) -> None:
    """An admin an operator promoted keeps that role when the provider re-pushes."""
    email = f"admin-{uuid.uuid4().hex[:8]}@tracecat.com"
    user = User(id=uuid.uuid4(), email=email, hashed_password="x")
    session.add(user)
    await session.flush()
    await seed_system_roles_for_org(session, org.id)
    admin_role_id = (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "organization-admin",
            )
        )
    ).scalar_one()
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=None,
            role_id=admin_role_id,
        )
    )
    await session.flush()

    await service.provision_user(external_id="idp-8", email=email)

    slug = (
        await session.execute(
            select(DBRole.slug)
            .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
            .where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.organization_id == org.id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
    ).scalar_one()
    assert slug == "organization-admin"


@pytest.mark.anyio
async def test_non_email_username_is_rejected(
    service: ScimProvisioningService,
) -> None:
    """A bare username has no address to store, so it fails validation."""
    with pytest.raises(TracecatValidationError):
        await service.provision_user(external_id="idp-9", email="not-an-email")
