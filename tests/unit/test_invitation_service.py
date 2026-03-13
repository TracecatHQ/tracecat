"""Tests for unified invitation service regressions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tracecat.invitations.service as invitation_service_module
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.db.models import (
    Invitation,
    Membership,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import (
    Role as DBRole,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate, WorkspaceAssignment
from tracecat.invitations.service import (
    InvitationService,
    accept_invitation_for_user,
    get_invitation_group_by_token,
    list_pending_invitation_groups_for_email,
)

pytestmark = pytest.mark.usefixtures("db")


def _create_user(*, email: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )


def _create_role_context(
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    scopes: frozenset[str] = ORG_ADMIN_SCOPES,
) -> Role:
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        scopes=scopes,
    )


@pytest.mark.anyio
async def test_create_invitation_blocks_owner_role_without_assign_scope(
    session: AsyncSession,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    inviter = _create_user(email="admin@example.com")
    owner_role = DBRole(
        id=uuid.uuid4(),
        name="Organization Owner",
        slug="organization-owner",
        description="Owner role",
        organization_id=organization.id,
    )
    session.add_all([organization, inviter])
    await session.flush()
    session.add(owner_role)
    await session.commit()

    service = InvitationService(
        session,
        role=_create_role_context(
            organization_id=organization.id,
            user_id=inviter.id,
        ),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match="Only organization owners can create owner invitations",
    ):
        await service.create_invitation(
            InvitationCreate(
                email="new-owner@example.com",
                role_id=owner_role.id,
            )
        )


def test_invitation_create_rejects_workspace_assignments_with_workspace_id() -> None:
    with pytest.raises(
        ValueError,
        match="workspace_assignments cannot be combined with workspace_id",
    ):
        InvitationCreate(
            email="invitee@example.com",
            role_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            workspace_assignments=[
                WorkspaceAssignment(
                    workspace_id=uuid.uuid4(),
                    role_id=uuid.uuid4(),
                )
            ],
        )


@pytest.mark.anyio
async def test_get_invitation_group_by_token_excludes_expired_workspace_options(
    session: AsyncSession,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Ops",
        organization_id=organization.id,
    )
    expired_workspace = Workspace(
        id=uuid.uuid4(),
        name="Old Ops",
        organization_id=organization.id,
    )
    org_role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        description="Member role",
        organization_id=organization.id,
    )
    workspace_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Editor",
        slug="workspace-editor",
        description="Workspace role",
        organization_id=organization.id,
    )
    org_invitation = Invitation(
        organization_id=organization.id,
        email="invitee@example.com",
        role_id=org_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    expired_workspace_invitation = Invitation(
        organization_id=organization.id,
        workspace_id=expired_workspace.id,
        email="invitee@example.com",
        role_id=workspace_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    valid_workspace_invitation = Invitation(
        organization_id=organization.id,
        workspace_id=workspace.id,
        email="invitee@example.com",
        role_id=workspace_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add_all([organization, workspace, expired_workspace])
    await session.flush()
    session.add_all(
        [
            org_role,
            workspace_role,
            org_invitation,
            expired_workspace_invitation,
            valid_workspace_invitation,
        ]
    )
    await session.commit()

    group = await get_invitation_group_by_token(session, token=org_invitation.token)

    assert group.invitation.id == org_invitation.id
    assert [row.id for row in group.workspace_invitations] == [
        valid_workspace_invitation.id
    ]


@pytest.mark.anyio
async def test_list_pending_invitation_groups_for_email_excludes_expired_workspace_rows(
    session: AsyncSession,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Ops",
        organization_id=organization.id,
    )
    org_role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        description="Member role",
        organization_id=organization.id,
    )
    workspace_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Editor",
        slug="workspace-editor",
        description="Workspace role",
        organization_id=organization.id,
    )
    session.add_all([organization, workspace])
    await session.flush()
    session.add_all([org_role, workspace_role])
    await session.flush()

    session.add_all(
        [
            Invitation(
                organization_id=organization.id,
                email="invitee@example.com",
                role_id=org_role.id,
                status=InvitationStatus.PENDING,
                token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
                expires_at=datetime.now(UTC) + timedelta(days=7),
            ),
            Invitation(
                organization_id=organization.id,
                workspace_id=workspace.id,
                email="invitee@example.com",
                role_id=workspace_role.id,
                status=InvitationStatus.PENDING,
                token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            ),
        ]
    )
    await session.commit()

    groups = await list_pending_invitation_groups_for_email(
        session,
        email="invitee@example.com",
    )

    assert len(groups) == 1
    assert groups[0].workspace_invitations == []


@pytest.mark.anyio
async def test_workspace_token_ignores_expired_org_parent(
    session: AsyncSession,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Ops",
        organization_id=organization.id,
    )
    org_role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        description="Member role",
        organization_id=organization.id,
    )
    workspace_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Editor",
        slug="workspace-editor",
        description="Workspace role",
        organization_id=organization.id,
    )
    expired_org_invitation = Invitation(
        organization_id=organization.id,
        email="invitee@example.com",
        role_id=org_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    workspace_invitation = Invitation(
        organization_id=organization.id,
        workspace_id=workspace.id,
        email="invitee@example.com",
        role_id=workspace_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add_all([organization, workspace])
    await session.flush()
    session.add_all(
        [
            org_role,
            workspace_role,
            expired_org_invitation,
            workspace_invitation,
        ]
    )
    await session.commit()

    group = await get_invitation_group_by_token(
        session, token=workspace_invitation.token
    )

    assert group.invitation.id == workspace_invitation.id
    assert group.redirected is False


@pytest.mark.anyio
async def test_accept_workspace_invitation_rejects_existing_membership(
    session: AsyncSession,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Ops",
        organization_id=organization.id,
    )
    invitee = _create_user(email="invitee@example.com")
    low_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Viewer",
        slug="workspace-viewer",
        description="Viewer role",
        organization_id=organization.id,
    )
    high_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Admin",
        slug="workspace-admin",
        description="Admin role",
        organization_id=organization.id,
    )
    invitation = Invitation(
        organization_id=organization.id,
        workspace_id=workspace.id,
        email=invitee.email,
        role_id=high_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add_all([organization, workspace, invitee])
    await session.flush()
    session.add_all(
        [
            low_role,
            high_role,
            invitation,
            OrganizationMembership(
                organization_id=organization.id,
                user_id=invitee.id,
            ),
            Membership(
                workspace_id=workspace.id,
                user_id=invitee.id,
            ),
            UserRoleAssignment(
                organization_id=organization.id,
                user_id=invitee.id,
                workspace_id=workspace.id,
                role_id=low_role.id,
            ),
        ]
    )
    await session.commit()
    invitation_id = invitation.id
    organization_id = organization.id
    workspace_id = workspace.id
    invitee_id = invitee.id
    low_role_id = low_role.id

    with pytest.raises(
        TracecatValidationError,
        match="User is already a member of this workspace",
    ):
        await accept_invitation_for_user(
            session,
            user_id=invitee.id,
            token=invitation.token,
        )
    await session.rollback()

    refreshed_invitation = await session.get(Invitation, invitation_id)
    assert refreshed_invitation is not None
    assert refreshed_invitation.status == InvitationStatus.PENDING

    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.user_id == invitee_id,
            UserRoleAssignment.workspace_id == workspace_id,
        )
    )
    assert assignment is not None
    assert assignment.role_id == low_role_id


@pytest.mark.anyio
async def test_accept_workspace_invitation_rolls_back_on_status_conflict(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Ops",
        organization_id=organization.id,
    )
    invitee = _create_user(email="invitee@example.com")
    workspace_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Editor",
        slug="workspace-editor",
        description="Editor role",
        organization_id=organization.id,
    )
    invitation = Invitation(
        organization_id=organization.id,
        workspace_id=workspace.id,
        email=invitee.email,
        role_id=workspace_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add_all([organization, workspace, invitee])
    await session.flush()
    session.add_all([workspace_role, invitation])
    await session.commit()
    invitation_id = invitation.id
    organization_id = organization.id
    workspace_id = workspace.id
    invitee_id = invitee.id

    original = invitation_service_module._set_invitation_status_if_pending

    async def fail_status_update(*args, **kwargs):  # noqa: ANN002, ANN003
        raise TracecatAuthorizationError(
            "Invitation is no longer pending for this action"
        )

    monkeypatch.setattr(
        invitation_service_module,
        "_set_invitation_status_if_pending",
        fail_status_update,
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match="Invitation is no longer pending for this action",
    ):
        await accept_invitation_for_user(
            session,
            user_id=invitee.id,
            token=invitation.token,
        )
    await session.rollback()

    monkeypatch.setattr(
        invitation_service_module,
        "_set_invitation_status_if_pending",
        original,
    )

    org_membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == invitee_id,
        )
    )
    workspace_membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == invitee_id,
        )
    )
    refreshed_invitation = await session.get(Invitation, invitation_id)

    assert org_membership is None
    assert workspace_membership is None
    assert refreshed_invitation is not None
    assert refreshed_invitation.status == InvitationStatus.PENDING


@pytest.mark.anyio
async def test_accept_org_invitation_rejects_unknown_selected_workspace_ids(
    session: AsyncSession,
) -> None:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Ops",
        organization_id=organization.id,
    )
    invitee = _create_user(email="invitee@example.com")
    org_role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        description="Member role",
        organization_id=organization.id,
    )
    workspace_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Editor",
        slug="workspace-editor",
        description="Workspace role",
        organization_id=organization.id,
    )
    org_invitation = Invitation(
        organization_id=organization.id,
        email=invitee.email,
        role_id=org_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    workspace_invitation = Invitation(
        organization_id=organization.id,
        workspace_id=workspace.id,
        email=invitee.email,
        role_id=workspace_role.id,
        status=InvitationStatus.PENDING,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add_all([organization, workspace, invitee])
    await session.flush()
    session.add_all([org_role, workspace_role, org_invitation, workspace_invitation])
    await session.commit()
    org_invitation_id = org_invitation.id
    workspace_invitation_id = workspace_invitation.id
    invitee_id = invitee.id
    organization_id = organization.id

    with pytest.raises(
        TracecatValidationError,
        match="Selected workspaces are not part of this invitation",
    ):
        await accept_invitation_for_user(
            session,
            user_id=invitee.id,
            token=org_invitation.token,
            selected_workspace_ids=[uuid.uuid4()],
        )
    await session.rollback()

    refreshed_org_invitation = await session.get(Invitation, org_invitation_id)
    refreshed_workspace_invitation = await session.get(
        Invitation, workspace_invitation_id
    )
    org_membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == invitee_id,
        )
    )

    assert refreshed_org_invitation is not None
    assert refreshed_org_invitation.status == InvitationStatus.PENDING
    assert refreshed_workspace_invitation is not None
    assert refreshed_workspace_invitation.status == InvitationStatus.PENDING
    assert org_membership is None
