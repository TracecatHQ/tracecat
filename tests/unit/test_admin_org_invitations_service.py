"""Tests for platform admin organization invitation service behavior."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.organizations.schemas import AdminOrgInvitationCreate
from tracecat_ee.admin.organizations.service import AdminOrgService

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    User,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
from tracecat.invitations.enums import InvitationStatus


@pytest.fixture
async def platform_admin(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"platform-admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Platform Invite Org",
        slug=f"platform-invite-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def org_roles(session: AsyncSession, org: Organization) -> dict[str, DBRole]:
    roles = {
        slug: DBRole(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            description=f"{name} role",
            organization_id=org.id,
        )
        for slug, name in {
            "organization-owner": "Organization Owner",
            "organization-admin": "Organization Admin",
            "organization-member": "Organization Member",
        }.items()
    }
    session.add_all(roles.values())
    await session.commit()
    return roles


@pytest.fixture
async def platform_role(platform_admin: User) -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=platform_admin.id,
        service_id="tracecat-api",
    )


@pytest.mark.anyio
async def test_create_organization_invitation_defaults_to_owner_role(
    session: AsyncSession,
    org: Organization,
    org_roles: dict[str, DBRole],
    platform_role: PlatformRole,
) -> None:
    service = AdminOrgService(session, platform_role)

    invitation = await service.create_organization_invitation(
        org.id,
        AdminOrgInvitationCreate(email="owner@example.com"),
    )

    assert invitation.email == "owner@example.com"
    assert invitation.role_slug == "organization-owner"
    assert invitation.role_id == org_roles["organization-owner"].id
    assert invitation.token
    assert invitation.created_by_platform_admin is True

    db_invitation = await session.scalar(
        select(OrganizationInvitation).where(OrganizationInvitation.id == invitation.id)
    )
    assert db_invitation is not None
    assert db_invitation.created_by_platform_admin is True
    assert db_invitation.invited_by == platform_role.user_id


@pytest.mark.anyio
async def test_create_organization_invitation_rejects_duplicate_pending_invite(
    session: AsyncSession,
    org: Organization,
    org_roles: dict[str, DBRole],
    platform_role: PlatformRole,
) -> None:
    service = AdminOrgService(session, platform_role)
    params = AdminOrgInvitationCreate(
        email="duplicate@example.com",
        role_slug="organization-admin",
    )
    await service.create_organization_invitation(org.id, params)

    with pytest.raises(
        TracecatValidationError,
        match="An invitation already exists for duplicate@example.com",
    ):
        await service.create_organization_invitation(org.id, params)

    invitations = (
        await session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == org.id,
                OrganizationInvitation.role_id == org_roles["organization-admin"].id,
            )
        )
    ).scalars()
    assert len(list(invitations)) == 1


@pytest.mark.anyio
async def test_create_organization_invitation_rejects_existing_member(
    session: AsyncSession,
    org: Organization,
    org_roles: dict[str, DBRole],
    platform_role: PlatformRole,
) -> None:
    member = User(
        id=uuid.uuid4(),
        email="member@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(member)
    await session.flush()
    session.add(OrganizationMembership(user_id=member.id, organization_id=org.id))
    await session.commit()

    service = AdminOrgService(session, platform_role)
    with pytest.raises(
        TracecatValidationError,
        match="member@example.com is already a member",
    ):
        await service.create_organization_invitation(
            org.id,
            AdminOrgInvitationCreate(
                email=member.email,
                role_slug="organization-member",
            ),
        )

    assert org_roles["organization-member"].organization_id == org.id


@pytest.mark.anyio
async def test_list_organization_invitations_only_returns_platform_created(
    session: AsyncSession,
    org: Organization,
    org_roles: dict[str, DBRole],
    platform_role: PlatformRole,
) -> None:
    service = AdminOrgService(session, platform_role)
    platform_invitation = await service.create_organization_invitation(
        org.id,
        AdminOrgInvitationCreate(email="platform@example.com"),
    )
    tenant_invitation = OrganizationInvitation(
        organization_id=org.id,
        email="tenant@example.com",
        role_id=org_roles["organization-member"].id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        status=InvitationStatus.PENDING,
        created_by_platform_admin=False,
    )
    session.add(tenant_invitation)
    await session.commit()

    invitations = await service.list_organization_invitations(org.id)

    assert [inv.id for inv in invitations] == [platform_invitation.id]


@pytest.mark.anyio
async def test_token_endpoint_only_exposes_platform_created_invitations(
    session: AsyncSession,
    org: Organization,
    org_roles: dict[str, DBRole],
    platform_role: PlatformRole,
) -> None:
    service = AdminOrgService(session, platform_role)
    platform_invitation = await service.create_organization_invitation(
        org.id,
        AdminOrgInvitationCreate(email="token@example.com"),
    )
    tenant_invitation = OrganizationInvitation(
        organization_id=org.id,
        email="tenant-token@example.com",
        role_id=org_roles["organization-member"].id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        status=InvitationStatus.PENDING,
        created_by_platform_admin=False,
    )
    session.add(tenant_invitation)
    await session.commit()

    token = await service.get_organization_invitation_token(
        org.id,
        platform_invitation.id,
    )
    assert token.token == platform_invitation.token

    with pytest.raises(NoResultFound):
        await service.get_organization_invitation_token(org.id, tenant_invitation.id)


@pytest.mark.anyio
async def test_revoke_organization_invitation_only_revokes_pending_platform_invites(
    session: AsyncSession,
    org: Organization,
    org_roles: dict[str, DBRole],
    platform_role: PlatformRole,
) -> None:
    service = AdminOrgService(session, platform_role)
    platform_invitation = await service.create_organization_invitation(
        org.id,
        AdminOrgInvitationCreate(email="revoke@example.com"),
    )

    await service.revoke_organization_invitation(org.id, platform_invitation.id)
    db_invitation = await session.scalar(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == platform_invitation.id
        )
    )
    assert db_invitation is not None
    assert db_invitation.status == InvitationStatus.REVOKED

    tenant_invitation = OrganizationInvitation(
        organization_id=org.id,
        email="tenant-revoke@example.com",
        role_id=org_roles["organization-member"].id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        status=InvitationStatus.PENDING,
        created_by_platform_admin=False,
    )
    session.add(tenant_invitation)
    await session.commit()

    with pytest.raises(NoResultFound):
        await service.revoke_organization_invitation(org.id, tenant_invitation.id)

    with pytest.raises(TracecatValidationError, match="Cannot revoke invitation"):
        await service.revoke_organization_invitation(org.id, platform_invitation.id)
