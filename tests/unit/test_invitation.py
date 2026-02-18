"""Tests for invitation models."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.db.models import (
    Invitation,
    Organization,
    OrganizationInvitation,
    Role,
    User,
    Workspace,
)
from tracecat.invitations.enums import InvitationStatus


def _create_org_role(
    organization_id: uuid.UUID,
    *,
    name: str = "Organization Member",
    slug: str = "organization-member",
) -> Role:
    """Create a Role DB record for testing."""
    return Role(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        organization_id=organization_id,
    )


def _create_workspace_role(
    organization_id: uuid.UUID,
    *,
    name: str = "Workspace Editor",
    slug: str = "workspace-editor",
) -> Role:
    """Create a Role DB record for testing."""
    return Role(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        organization_id=organization_id,
    )


class TestOrganizationInvitation:
    """Tests for OrganizationInvitation model."""

    @pytest.mark.anyio
    async def test_create_organization_invitation(self, session: AsyncSession):
        """Test creating an organization invitation."""
        # Create organization
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        # Create inviter user
        inviter = User(
            id=uuid.uuid4(),
            email=f"inviter-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(inviter)

        # Create RBAC role
        member_role = _create_org_role(org.id)
        session.add(member_role)
        await session.flush()

        # Create invitation
        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        expires_at = datetime.now(UTC) + timedelta(days=7)
        invitation = OrganizationInvitation(
            organization_id=org.id,
            email="invitee@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            invited_by=inviter.id,
            token=token,
            expires_at=expires_at,
        )
        session.add(invitation)
        await session.commit()
        await session.refresh(invitation)

        assert invitation.id is not None
        assert invitation.organization_id == org.id
        assert invitation.email == "invitee@example.com"
        assert invitation.role_id == member_role.id
        assert invitation.status == InvitationStatus.PENDING
        assert invitation.invited_by == inviter.id
        assert invitation.token == token
        assert invitation.accepted_at is None
        assert invitation.created_at is not None

    @pytest.mark.anyio
    async def test_organization_invitation_admin_role(self, session: AsyncSession):
        """Test creating an organization invitation with admin role."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        admin_role = _create_org_role(
            org.id, name="Organization Admin", slug="organization-admin"
        )
        session.add(admin_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation = OrganizationInvitation(
            organization_id=org.id,
            email="admin@example.com",
            role_id=admin_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()
        await session.refresh(invitation)

        assert invitation.role_id == admin_role.id

    @pytest.mark.anyio
    async def test_organization_invitation_status_transition(
        self, session: AsyncSession
    ):
        """Test invitation status can be updated."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        member_role = _create_org_role(org.id)
        session.add(member_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation = OrganizationInvitation(
            organization_id=org.id,
            email="invitee@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()

        # Accept the invitation
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(invitation)

        assert invitation.status == InvitationStatus.ACCEPTED
        assert invitation.accepted_at is not None

    @pytest.mark.anyio
    async def test_organization_invitation_cascade_delete(self, session: AsyncSession):
        """Test invitation is deleted when organization is deleted."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        member_role = _create_org_role(org.id)
        session.add(member_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation = OrganizationInvitation(
            organization_id=org.id,
            email="invitee@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()
        invitation_id = invitation.id

        # Delete the organization
        await session.delete(org)
        await session.commit()

        # Verify invitation is also deleted
        result = await session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.id == invitation_id
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_organization_invitation_unique_token(self, session: AsyncSession):
        """Test that invitation tokens must be unique."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        member_role = _create_org_role(org.id)
        session.add(member_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation1 = OrganizationInvitation(
            organization_id=org.id,
            email="invitee1@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation1)
        await session.commit()

        # Try to create another invitation with the same token
        invitation2 = OrganizationInvitation(
            organization_id=org.id,
            email="invitee2@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,  # Same token
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation2)

        with pytest.raises(IntegrityError):
            await session.commit()


class TestInvitation:
    """Tests for Invitation (workspace) model."""

    @pytest.mark.anyio
    async def test_create_workspace_invitation(self, session: AsyncSession):
        """Test creating a workspace invitation."""
        # Create organization
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)
        await session.flush()

        # Create workspace
        workspace = Workspace(
            id=uuid.uuid4(),
            name="Test Workspace",
            organization_id=org.id,
        )
        session.add(workspace)

        # Create inviter user
        inviter = User(
            id=uuid.uuid4(),
            email=f"inviter-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(inviter)

        # Create RBAC role
        editor_role = _create_workspace_role(org.id)
        session.add(editor_role)
        await session.flush()

        # Create invitation
        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        expires_at = datetime.now(UTC) + timedelta(days=7)
        invitation = Invitation(
            workspace_id=workspace.id,
            email="invitee@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            invited_by=inviter.id,
            token=token,
            expires_at=expires_at,
        )
        session.add(invitation)
        await session.commit()
        await session.refresh(invitation)

        assert invitation.id is not None
        assert invitation.workspace_id == workspace.id
        assert invitation.email == "invitee@example.com"
        assert invitation.role_id == editor_role.id
        assert invitation.status == InvitationStatus.PENDING
        assert invitation.invited_by == inviter.id
        assert invitation.token == token
        assert invitation.accepted_at is None
        assert invitation.created_at is not None

    @pytest.mark.anyio
    async def test_workspace_invitation_admin_role(self, session: AsyncSession):
        """Test creating a workspace invitation with admin role."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)
        await session.flush()

        workspace = Workspace(
            id=uuid.uuid4(),
            name="Test Workspace",
            organization_id=org.id,
        )
        session.add(workspace)

        admin_role = _create_workspace_role(
            org.id, name="Workspace Admin", slug="workspace-admin"
        )
        session.add(admin_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation = Invitation(
            workspace_id=workspace.id,
            email="admin@example.com",
            role_id=admin_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()
        await session.refresh(invitation)

        assert invitation.role_id == admin_role.id

    @pytest.mark.anyio
    async def test_workspace_invitation_status_transition(self, session: AsyncSession):
        """Test invitation status can be updated."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)
        await session.flush()

        workspace = Workspace(
            id=uuid.uuid4(),
            name="Test Workspace",
            organization_id=org.id,
        )
        session.add(workspace)

        editor_role = _create_workspace_role(org.id)
        session.add(editor_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation = Invitation(
            workspace_id=workspace.id,
            email="invitee@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()

        # Revoke the invitation
        invitation.status = InvitationStatus.REVOKED
        await session.commit()
        await session.refresh(invitation)

        assert invitation.status == InvitationStatus.REVOKED

    @pytest.mark.anyio
    async def test_workspace_invitation_cascade_delete(self, session: AsyncSession):
        """Test invitation is deleted when workspace is deleted."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)
        await session.flush()

        workspace = Workspace(
            id=uuid.uuid4(),
            name="Test Workspace",
            organization_id=org.id,
        )
        session.add(workspace)

        editor_role = _create_workspace_role(org.id)
        session.add(editor_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation = Invitation(
            workspace_id=workspace.id,
            email="invitee@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()
        invitation_id = invitation.id

        # Delete the workspace
        await session.delete(workspace)
        await session.commit()

        # Verify invitation is also deleted
        result = await session.execute(
            select(Invitation).where(Invitation.id == invitation_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_workspace_invitation_unique_token(self, session: AsyncSession):
        """Test that invitation tokens must be unique."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)
        await session.flush()

        workspace = Workspace(
            id=uuid.uuid4(),
            name="Test Workspace",
            organization_id=org.id,
        )
        session.add(workspace)

        editor_role = _create_workspace_role(org.id)
        session.add(editor_role)
        await session.flush()

        token = uuid.uuid4().hex + uuid.uuid4().hex[:32]
        invitation1 = Invitation(
            workspace_id=workspace.id,
            email="invitee1@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation1)
        await session.commit()

        # Try to create another invitation with the same token
        invitation2 = Invitation(
            workspace_id=workspace.id,
            email="invitee2@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            invited_by=None,
            token=token,  # Same token
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation2)

        with pytest.raises(IntegrityError):
            await session.commit()
