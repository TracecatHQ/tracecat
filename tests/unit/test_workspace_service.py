"""Tests for WorkspaceService functionality."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import (
    Membership,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.workspaces.schemas import (
    WorkspaceInvitationCreate,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)
from tracecat.workspaces.service import WorkspaceService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def service(session: AsyncSession, svc_role: Role) -> WorkspaceService:
    """Create a workspace service instance for testing."""
    return WorkspaceService(session=session, role=svc_role)


@pytest.mark.anyio
class TestWorkspaceService:
    """Test WorkspaceService methods."""

    async def test_get_workspace_success(
        self, service: WorkspaceService, svc_workspace: Workspace
    ) -> None:
        """Test successfully retrieving a workspace by ID."""
        # Test retrieving the existing workspace
        workspace = await service.get_workspace(svc_workspace.id)

        assert workspace is not None
        assert workspace.id == svc_workspace.id
        assert workspace.name == svc_workspace.name
        assert isinstance(workspace, Workspace)

    async def test_get_workspace_not_found(self, service: WorkspaceService) -> None:
        """Test getting a workspace that doesn't exist returns None."""
        non_existent_id = uuid.uuid4()

        workspace = await service.get_workspace(non_existent_id)

        assert workspace is None

    async def test_get_workspace_validates_model(
        self, service: WorkspaceService, svc_workspace: Workspace
    ) -> None:
        """Test that get_workspace properly validates the model when loading from database."""
        # First, retrieve the workspace to ensure it exists
        workspace = await service.get_workspace(svc_workspace.id)

        assert workspace is not None
        # Verify that the workspace was properly validated by checking it's a Workspace instance
        assert isinstance(workspace, Workspace)
        # The model_validate call ensures Pydantic validation was invoked
        assert hasattr(workspace, "id")
        assert hasattr(workspace, "name")
        assert hasattr(workspace, "settings")
        # Verify that settings is properly validated as WorkspaceSettings instance
        ta = TypeAdapter(WorkspaceSettings)
        assert ta.validate_python(workspace.settings) is not None

    async def test_get_workspace_model_validation_preserves_data(
        self, service: WorkspaceService, svc_workspace: Workspace
    ) -> None:
        """Test that model validation preserves workspace data correctly."""
        workspace = await service.get_workspace(svc_workspace.id)

        assert workspace is not None
        assert workspace.id == svc_workspace.id
        assert workspace.name == svc_workspace.name
        assert workspace.organization_id == svc_workspace.organization_id
        # Verify settings are preserved through validation
        assert workspace.settings is not None


@pytest.mark.parametrize(
    "valid_url",
    [
        "git+ssh://git@github.com/org/repo.git",
        "git+ssh://git@gitlab.company.com:2222/team/project.git",
        "git+ssh://git@gitlab.com/group/subgroup/repo.git",
        "git+ssh://git@example.com/org/repo",
    ],
)
def test_workspace_settings_update_accepts_valid_git_urls(valid_url: str) -> None:
    """Workspace settings should accept git+ssh URLs that match the shared regex."""
    settings = WorkspaceSettingsUpdate(git_repo_url=valid_url)

    assert settings.git_repo_url == valid_url


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://github.com/org/repo.git",
        "git+ssh://user@github.com/org/repo.git",
        "git+ssh://git@github.com",
        "git+ssh://git@github.com:not_a_port/org/repo.git",
        "git+ssh://git@github.com:/org/repo.git",
        "git+ssh://git@github.com/repo.git",
    ],
)
def test_workspace_settings_update_rejects_invalid_git_urls(invalid_url: str) -> None:
    """Workspace settings should reject malformed git repo URLs."""
    with pytest.raises(ValueError) as exc_info:
        WorkspaceSettingsUpdate(git_repo_url=invalid_url)

    assert "Must be a valid Git SSH URL" in str(exc_info.value)


# =============================================================================
# Invitation Tests
# =============================================================================


@pytest.fixture
async def inv_org(session: AsyncSession) -> Organization:
    """Create a test organization for invitation tests."""
    org = Organization(
        id=uuid.uuid4(),
        name="Test Organization",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def inv_workspace(session: AsyncSession, inv_org: Organization) -> Workspace:
    """Create a test workspace for invitation tests."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=inv_org.id,
    )
    session.add(workspace)
    await session.commit()
    return workspace


@pytest.fixture
async def admin_user(session: AsyncSession, inv_org: Organization) -> User:
    """Create an admin user in the organization."""
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=inv_org.id,
        role=OrgRole.ADMIN,
    )
    session.add(membership)
    await session.commit()
    return user


@pytest.fixture
async def basic_user(session: AsyncSession, inv_org: Organization) -> User:
    """Create a basic user in the organization."""
    user = User(
        id=uuid.uuid4(),
        email=f"basic-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=inv_org.id,
        role=OrgRole.MEMBER,
    )
    session.add(membership)
    await session.commit()
    return user


@pytest.fixture
async def external_user(session: AsyncSession) -> User:
    """Create a user not in any organization."""
    user = User(
        id=uuid.uuid4(),
        email=f"external-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


def create_workspace_admin_role(
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Role:
    """Create a workspace admin role for testing."""
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        workspace_role=WorkspaceRole.ADMIN,
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


@pytest.mark.anyio
class TestCreateInvitation:
    """Tests for WorkspaceService.create_invitation()."""

    async def test_create_invitation_success(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test creating a workspace invitation successfully."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email="invitee@example.com",
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        assert invitation.id is not None
        assert invitation.workspace_id == inv_workspace.id
        assert invitation.email == "invitee@example.com"
        assert invitation.role == WorkspaceRole.EDITOR
        assert invitation.status == InvitationStatus.PENDING
        assert invitation.invited_by == admin_user.id
        assert len(invitation.token) == 64
        assert invitation.expires_at > datetime.now(UTC)
        assert invitation.accepted_at is None

    async def test_create_invitation_duplicate_pending_fails(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test creating duplicate pending invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email="duplicate@example.com",
            role=WorkspaceRole.EDITOR,
        )

        # Create first invitation
        await service.create_invitation(inv_workspace.id, params)

        # Try to create duplicate - should fail
        with pytest.raises(
            TracecatValidationError, match="pending invitation already exists"
        ):
            await service.create_invitation(inv_workspace.id, params)

    async def test_create_invitation_unique_constraint_violation(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test that unique constraint catches duplicate email in same workspace.

        This tests the database-level constraint when the application-level
        pending check is bypassed (e.g., first invitation is revoked).
        """
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        email = "constraint-test@example.com"
        params = WorkspaceInvitationCreate(
            email=email,
            role=WorkspaceRole.EDITOR,
        )

        # Create first invitation
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Revoke it so the pending check passes
        await service.revoke_invitation(inv_workspace.id, invitation.id)

        # Try to create another invitation for the same email
        # This should fail due to the unique constraint (revoked != expired)
        with pytest.raises(TracecatValidationError, match="invitation already exists"):
            await service.create_invitation(inv_workspace.id, params)

    async def test_create_invitation_replaces_expired_invitation(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test that expired invitation is deleted and new one created."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        email = "expired-test@example.com"
        params = WorkspaceInvitationCreate(
            email=email,
            role=WorkspaceRole.EDITOR,
        )

        # Create first invitation
        invitation = await service.create_invitation(inv_workspace.id, params)
        old_id = invitation.id

        # Manually expire it (set expires_at to the past)
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        # Create another invitation for the same email
        # This should succeed because the old one is expired
        new_invitation = await service.create_invitation(inv_workspace.id, params)

        assert new_invitation.id != old_id
        assert new_invitation.email == email
        assert new_invitation.expires_at > datetime.now(UTC)


@pytest.mark.anyio
class TestListInvitations:
    """Tests for WorkspaceService.list_invitations()."""

    async def test_list_invitations_returns_workspace_invitations(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test listing invitations returns only workspace invitations."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        # Create some invitations
        for i in range(3):
            params = WorkspaceInvitationCreate(
                email=f"invitee{i}@example.com",
                role=WorkspaceRole.EDITOR,
            )
            await service.create_invitation(inv_workspace.id, params)

        invitations = await service.list_invitations(inv_workspace.id)

        assert len(invitations) == 3

    async def test_list_invitations_filter_by_status(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test filtering invitations by status."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        # Create a pending invitation
        params = WorkspaceInvitationCreate(
            email="pending@example.com",
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Manually revoke it
        invitation.status = InvitationStatus.REVOKED
        await session.commit()

        # Create another pending invitation
        params2 = WorkspaceInvitationCreate(
            email="pending2@example.com",
            role=WorkspaceRole.EDITOR,
        )
        await service.create_invitation(inv_workspace.id, params2)

        # List only pending
        pending = await service.list_invitations(
            inv_workspace.id, status=InvitationStatus.PENDING
        )
        assert len(pending) == 1
        assert pending[0].email == "pending2@example.com"

        # List only revoked
        revoked = await service.list_invitations(
            inv_workspace.id, status=InvitationStatus.REVOKED
        )
        assert len(revoked) == 1
        assert revoked[0].email == "pending@example.com"


@pytest.mark.anyio
class TestGetInvitationByToken:
    """Tests for WorkspaceService.get_invitation_by_token()."""

    async def test_get_invitation_by_token_success(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test retrieving an invitation by token."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email="token-test@example.com",
            role=WorkspaceRole.EDITOR,
        )
        created = await service.create_invitation(inv_workspace.id, params)

        # Retrieve by token
        retrieved = await service.get_invitation_by_token(created.token)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.workspace is not None

    async def test_get_invitation_by_token_not_found(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test retrieving non-existent token returns None."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        result = await service.get_invitation_by_token("nonexistent-token")
        assert result is None


@pytest.mark.anyio
class TestAcceptInvitation:
    """Tests for WorkspaceService.accept_invitation()."""

    async def test_accept_invitation_success_existing_org_member(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
    ):
        """Test accepting invitation as existing org member."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        # Create invitation for basic_user's email
        params = WorkspaceInvitationCreate(
            email=basic_user.email,
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Accept invitation
        membership = await service.accept_invitation(invitation.token, basic_user.id)

        assert membership.user_id == basic_user.id
        assert membership.workspace_id == inv_workspace.id
        assert membership.role == WorkspaceRole.EDITOR

        # Verify invitation was updated
        await session.refresh(invitation)
        assert invitation.status == InvitationStatus.ACCEPTED
        assert invitation.accepted_at is not None

    async def test_accept_invitation_auto_creates_org_membership(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        external_user: User,
    ):
        """Test accepting invitation auto-creates org membership for external user."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        # Create invitation for external user
        params = WorkspaceInvitationCreate(
            email=external_user.email,
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Accept invitation
        membership = await service.accept_invitation(invitation.token, external_user.id)

        assert membership.user_id == external_user.id

        # Verify org membership was created
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == external_user.id,
                OrganizationMembership.organization_id == inv_org.id,
            )
        )
        org_membership = result.scalar_one()
        assert org_membership.role == OrgRole.MEMBER

    async def test_accept_invitation_not_found(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test accepting non-existent invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await service.accept_invitation("invalid-token", admin_user.id)

    async def test_accept_invitation_already_accepted(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
    ):
        """Test accepting already-accepted invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email=basic_user.email,
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Accept once
        await service.accept_invitation(invitation.token, basic_user.id)

        # Try to accept again with different user
        another_user = User(
            id=uuid.uuid4(),
            email=f"another-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(another_user)
        await session.commit()

        with pytest.raises(TracecatValidationError, match="already been accepted"):
            await service.accept_invitation(invitation.token, another_user.id)

    async def test_accept_invitation_revoked(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
    ):
        """Test accepting revoked invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email=basic_user.email,
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Revoke invitation
        await service.revoke_invitation(inv_workspace.id, invitation.id)

        with pytest.raises(TracecatValidationError, match="has been revoked"):
            await service.accept_invitation(invitation.token, basic_user.id)

    async def test_accept_invitation_user_already_member(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
    ):
        """Test accepting invitation when user is already a workspace member fails."""
        # Add basic_user to workspace
        ws_membership = Membership(
            user_id=basic_user.id,
            workspace_id=inv_workspace.id,
            role=WorkspaceRole.EDITOR,
        )
        session.add(ws_membership)
        await session.commit()

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email=basic_user.email,
            role=WorkspaceRole.ADMIN,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        with pytest.raises(
            TracecatValidationError, match="already a member of this workspace"
        ):
            await service.accept_invitation(invitation.token, basic_user.id)


@pytest.mark.anyio
class TestRevokeInvitation:
    """Tests for WorkspaceService.revoke_invitation()."""

    async def test_revoke_invitation_success(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test revoking a pending invitation."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email="to-revoke@example.com",
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        await service.revoke_invitation(inv_workspace.id, invitation.id)

        await session.refresh(invitation)
        assert invitation.status == InvitationStatus.REVOKED

    async def test_revoke_invitation_not_found(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test revoking non-existent invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await service.revoke_invitation(inv_workspace.id, uuid.uuid4())

    async def test_revoke_invitation_already_accepted(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
    ):
        """Test revoking already-accepted invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email=basic_user.email,
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Accept the invitation
        await service.accept_invitation(invitation.token, basic_user.id)

        # Try to revoke
        with pytest.raises(
            TracecatValidationError, match="Cannot revoke invitation with status"
        ):
            await service.revoke_invitation(inv_workspace.id, invitation.id)

    async def test_revoke_invitation_already_revoked(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test revoking already-revoked invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = WorkspaceService(session, role=role)

        params = WorkspaceInvitationCreate(
            email="revoke-twice@example.com",
            role=WorkspaceRole.EDITOR,
        )
        invitation = await service.create_invitation(inv_workspace.id, params)

        # Revoke once
        await service.revoke_invitation(inv_workspace.id, invitation.id)

        # Try to revoke again
        with pytest.raises(
            TracecatValidationError, match="Cannot revoke invitation with status"
        ):
            await service.revoke_invitation(inv_workspace.id, invitation.id)


class TestTokenGeneration:
    """Tests for invitation token generation."""

    def test_generate_token_length(self):
        """Test that generated token has correct length."""
        token = WorkspaceService._generate_invitation_token()
        assert len(token) == 64

    def test_generate_token_unique(self):
        """Test that generated tokens are unique."""
        tokens = {WorkspaceService._generate_invitation_token() for _ in range(100)}
        assert len(tokens) == 100  # All unique
