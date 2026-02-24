"""Tests for WorkspaceService functionality."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
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
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate
from tracecat.invitations.service import InvitationService
from tracecat.workspaces.schemas import (
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
async def rbac_roles(
    session: AsyncSession, inv_org: Organization
) -> dict[str, uuid.UUID]:
    """Create RBAC Role records for the test organization.

    Returns a dict mapping role slug to role id (as UUID).
    """
    roles: dict[str, uuid.UUID] = {}
    for slug, name in [
        ("organization-member", "Organization Member"),
        ("organization-admin", "Organization Admin"),
        ("workspace-editor", "Workspace Editor"),
        ("workspace-admin", "Workspace Admin"),
        ("workspace-viewer", "Workspace Viewer"),
    ]:
        role = DBRole(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            description=f"Test {name} role",
            organization_id=inv_org.id,
        )
        session.add(role)
        roles[slug] = role.id
    await session.commit()
    return roles


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
async def foreign_org(session: AsyncSession) -> Organization:
    """Create another organization for cross-org access tests."""
    org = Organization(
        id=uuid.uuid4(),
        name="Foreign Organization",
        slug=f"foreign-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def foreign_workspace(
    session: AsyncSession, foreign_org: Organization
) -> Workspace:
    """Create a workspace in another organization."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Foreign Workspace",
        organization_id=foreign_org.id,
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
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


@pytest.mark.anyio
class TestCreateInvitation:
    """Tests for InvitationService.create_email_invitation()."""

    async def test_create_invitation_success(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test creating a workspace invitation successfully."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email="invitee@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        assert invitation.id is not None
        assert invitation.workspace_id == inv_workspace.id
        assert invitation.email == "invitee@example.com"
        assert invitation.role_id == rbac_roles["workspace-editor"]
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
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test creating duplicate pending invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        # Create first invitation
        await service._create_email_invitation(
            inv_workspace.id,
            email="duplicate@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        # Try to create duplicate - should fail
        with pytest.raises(
            TracecatValidationError, match="pending invitation already exists"
        ):
            await service._create_email_invitation(
                inv_workspace.id,
                email="duplicate@example.com",
                role_id=rbac_roles["workspace-editor"],
            )

    async def test_create_invitation_unique_constraint_violation(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test that unique constraint catches duplicate email in same workspace.

        This tests the database-level constraint when the application-level
        pending check is bypassed (e.g., first invitation is revoked).
        """
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        email = "constraint-test@example.com"

        # Create first invitation
        invitation = await service._create_email_invitation(
            inv_workspace.id, email=email, role_id=rbac_roles["workspace-editor"]
        )

        # Revoke it so the pending check passes
        await service.revoke_invitation(invitation.id)

        # Try to create another invitation for the same email
        # This should fail due to the unique constraint (revoked != expired)
        with pytest.raises(TracecatValidationError, match="invitation already exists"):
            await service._create_email_invitation(
                inv_workspace.id, email=email, role_id=rbac_roles["workspace-editor"]
            )

    async def test_create_invitation_replaces_expired_invitation(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test that expired invitation is deleted and new one created."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        email = "expired-test@example.com"

        # Create first invitation
        invitation = await service._create_email_invitation(
            inv_workspace.id, email=email, role_id=rbac_roles["workspace-editor"]
        )
        old_id = invitation.id

        # Manually expire it (set expires_at to the past)
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        # Create another invitation for the same email
        # This should succeed because the old one is expired
        new_invitation = await service._create_email_invitation(
            inv_workspace.id, email=email, role_id=rbac_roles["workspace-editor"]
        )

        assert new_invitation.id != old_id
        assert new_invitation.email == email
        assert new_invitation.expires_at > datetime.now(UTC)

    async def test_create_workspace_invitation_rejects_foreign_workspace(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        foreign_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Creating workspace invitations for foreign-org workspaces is denied."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="Workspace does not belong to this organization",
        ):
            await service.create_workspace_invitation(
                foreign_workspace.id,
                InvitationCreate(
                    email="foreign-ws@example.com",
                    role_id=rbac_roles["workspace-editor"],
                    workspace_id=foreign_workspace.id,
                ),
            )

    async def test_create_workspace_invitation_direct_add_uses_workspace_scopes(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
        rbac_roles: dict[str, uuid.UUID],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Direct-add path should use workspace-effective scopes for authorization."""

        async def mock_compute_workspace_effective_scopes(
            _session: AsyncSession,
            *,
            role: Role,
            workspace_id: uuid.UUID,
        ) -> frozenset[str]:
            assert role.user_id == admin_user.id
            assert workspace_id == inv_workspace.id
            return frozenset({"workspace:member:invite"})

        monkeypatch.setattr(
            "tracecat.invitations.service._compute_workspace_effective_scopes",
            mock_compute_workspace_effective_scopes,
        )

        role = Role(
            type="user",
            user_id=admin_user.id,
            organization_id=inv_org.id,
            service_id="tracecat-api",
            scopes=frozenset({"org:member:invite"}),
        )
        service = InvitationService(session, role=role)

        result = await service.create_workspace_invitation(
            inv_workspace.id,
            InvitationCreate(
                email=basic_user.email,
                role_id=rbac_roles["workspace-editor"],
                workspace_id=inv_workspace.id,
            ),
        )
        assert result is None

        membership_result = await session.execute(
            select(Membership).where(
                Membership.workspace_id == inv_workspace.id,
                Membership.user_id == basic_user.id,
            )
        )
        assert membership_result.scalar_one_or_none() is not None


@pytest.mark.anyio
class TestListInvitations:
    """Tests for InvitationService.list_workspace_invitations()."""

    async def test_list_invitations_returns_workspace_invitations(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test listing invitations returns only workspace invitations."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        # Create some invitations
        for i in range(3):
            await service._create_email_invitation(
                inv_workspace.id,
                email=f"invitee{i}@example.com",
                role_id=rbac_roles["workspace-editor"],
            )

        invitations = await service.list_workspace_invitations(inv_workspace.id)

        assert len(invitations) == 3

    async def test_list_invitations_rejects_foreign_workspace(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        foreign_workspace: Workspace,
        admin_user: User,
    ):
        """Listing workspace invitations for foreign-org workspaces is denied."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="Workspace does not belong to this organization",
        ):
            await service.list_workspace_invitations(foreign_workspace.id)

    async def test_list_invitations_filter_by_status(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test filtering invitations by status."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        # Create a pending invitation
        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email="pending@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        # Manually revoke it
        invitation.status = InvitationStatus.REVOKED
        await session.commit()

        # Create another pending invitation
        await service._create_email_invitation(
            inv_workspace.id,
            email="pending2@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        # List only pending
        pending = await service.list_workspace_invitations(
            inv_workspace.id, status=InvitationStatus.PENDING
        )
        assert len(pending) == 1
        assert pending[0].email == "pending2@example.com"

        # List only revoked
        revoked = await service.list_workspace_invitations(
            inv_workspace.id, status=InvitationStatus.REVOKED
        )
        assert len(revoked) == 1
        assert revoked[0].email == "pending@example.com"


@pytest.mark.anyio
class TestGetInvitationById:
    """Tests for InvitationService.get_invitation()."""

    async def test_get_invitation_success(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test retrieving a workspace invitation by ID."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        created = await service._create_email_invitation(
            inv_workspace.id,
            email="token-test@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        retrieved = await service.get_invitation(created.id)

        assert retrieved.id == created.id
        assert retrieved.workspace_id == inv_workspace.id

    async def test_get_invitation_not_found(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test retrieving non-existent invitation raises error."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await service.get_invitation(uuid.uuid4())


@pytest.mark.anyio
class TestAcceptInvitation:
    """Tests for accept_invitation_for_user()."""

    async def test_accept_invitation_success_existing_org_member(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test accepting invitation as existing org member."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        # Create invitation for basic_user's email
        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=basic_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        # Accept invitation
        membership = await accept_invitation_for_user(
            session, user_id=basic_user.id, token=invitation.token
        )

        assert membership.user_id == basic_user.id
        assert isinstance(membership, Membership)
        assert membership.workspace_id == inv_workspace.id

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
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test accepting invitation auto-creates org membership for external user."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        # Create invitation for external user
        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=external_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        # Accept invitation
        membership = await accept_invitation_for_user(
            session, user_id=external_user.id, token=invitation.token
        )

        assert membership.user_id == external_user.id

        # Verify org membership was created
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == external_user.id,
                OrganizationMembership.organization_id == inv_org.id,
            )
        )
        org_membership = result.scalar_one()
        assert org_membership.user_id == external_user.id
        assert org_membership.organization_id == inv_org.id

    async def test_accept_workspace_invitation_applies_pending_org_invite_role(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        external_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Auto-accepted org invitations should preserve their requested org role."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        ws_invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=external_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        org_invitation = Invitation(
            id=uuid.uuid4(),
            organization_id=inv_org.id,
            workspace_id=None,
            email=external_user.email.lower(),
            role_id=rbac_roles["organization-admin"],
            status=InvitationStatus.PENDING,
            invited_by=admin_user.id,
            token=f"org-role-{uuid.uuid4().hex}"[:64],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(org_invitation)
        await session.commit()

        await accept_invitation_for_user(
            session, user_id=external_user.id, token=ws_invitation.token
        )

        org_assignment_result = await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.organization_id == inv_org.id,
                UserRoleAssignment.user_id == external_user.id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
        org_assignment = org_assignment_result.scalar_one_or_none()
        assert org_assignment is not None
        assert org_assignment.role_id == rbac_roles["organization-admin"]

        await session.refresh(org_invitation)
        assert org_invitation.status == InvitationStatus.ACCEPTED
        assert org_invitation.accepted_at is not None

    async def test_accept_org_invitation_ignores_expired_workspace_invites(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        external_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Expired workspace invites should not be auto-accepted on org invite accept."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        ws_invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=external_user.email,
            role_id=rbac_roles["workspace-editor"],
        )
        ws_invitation.expires_at = datetime.now(UTC) - timedelta(days=1)

        org_invitation = Invitation(
            id=uuid.uuid4(),
            organization_id=inv_org.id,
            workspace_id=None,
            email=external_user.email.lower(),
            role_id=rbac_roles["organization-member"],
            status=InvitationStatus.PENDING,
            invited_by=admin_user.id,
            token=f"org-cascade-{uuid.uuid4().hex}"[:64],
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(org_invitation)
        await session.commit()

        await accept_invitation_for_user(
            session, user_id=external_user.id, token=org_invitation.token
        )

        workspace_membership_result = await session.execute(
            select(Membership).where(
                Membership.user_id == external_user.id,
                Membership.workspace_id == inv_workspace.id,
            )
        )
        assert workspace_membership_result.scalar_one_or_none() is None

        await session.refresh(ws_invitation)
        assert ws_invitation.status == InvitationStatus.PENDING
        assert ws_invitation.accepted_at is None

    async def test_accept_workspace_invitation_ignores_expired_org_invite_role(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        external_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Expired org invites should not drive org role derivation on workspace accept."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        ws_invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=external_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        expired_org_invitation = Invitation(
            id=uuid.uuid4(),
            organization_id=inv_org.id,
            workspace_id=None,
            email=external_user.email.lower(),
            role_id=rbac_roles["organization-admin"],
            status=InvitationStatus.PENDING,
            invited_by=admin_user.id,
            token=f"org-expired-{uuid.uuid4().hex}"[:64],
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        session.add(expired_org_invitation)
        await session.commit()

        await accept_invitation_for_user(
            session, user_id=external_user.id, token=ws_invitation.token
        )

        org_assignment_result = await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.organization_id == inv_org.id,
                UserRoleAssignment.user_id == external_user.id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
        org_assignment = org_assignment_result.scalar_one_or_none()
        assert org_assignment is not None
        assert org_assignment.role_id == rbac_roles["organization-member"]

        await session.refresh(expired_org_invitation)
        assert expired_org_invitation.status == InvitationStatus.PENDING
        assert expired_org_invitation.accepted_at is None

    async def test_accept_invitation_not_found(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
    ):
        """Test accepting non-existent invitation fails."""
        from tracecat.invitations.service import accept_invitation_for_user

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await accept_invitation_for_user(
                session, user_id=admin_user.id, token="invalid-token"
            )

    async def test_accept_invitation_already_accepted(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test accepting already-accepted invitation fails."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=basic_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        # Accept once
        await accept_invitation_for_user(
            session, user_id=basic_user.id, token=invitation.token
        )

        # Try to accept again with the same user — should fail because
        # the optimistic UPDATE finds status != PENDING
        with pytest.raises(TracecatAuthorizationError, match="already been accepted"):
            await accept_invitation_for_user(
                session, user_id=basic_user.id, token=invitation.token
            )

    async def test_accept_invitation_revoked(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test accepting revoked invitation fails."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=basic_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        # Revoke invitation
        await service.revoke_invitation(invitation.id)

        with pytest.raises(TracecatAuthorizationError, match="has been revoked"):
            await accept_invitation_for_user(
                session, user_id=basic_user.id, token=invitation.token
            )

    async def test_accept_invitation_user_already_member(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test accepting invitation when user is already a workspace member fails."""
        from tracecat.invitations.service import accept_invitation_for_user

        # Add basic_user to workspace
        ws_membership = Membership(
            user_id=basic_user.id,
            workspace_id=inv_workspace.id,
        )
        session.add(ws_membership)
        await session.commit()

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=basic_user.email,
            role_id=rbac_roles["workspace-admin"],
        )

        with pytest.raises(
            TracecatValidationError, match="already a member of this workspace"
        ):
            await accept_invitation_for_user(
                session, user_id=basic_user.id, token=invitation.token
            )


@pytest.mark.anyio
class TestRevokeInvitation:
    """Tests for InvitationService.revoke_invitation()."""

    async def test_revoke_invitation_success(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test revoking a pending invitation."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email="to-revoke@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        await service.revoke_invitation(invitation.id)

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
        service = InvitationService(session, role=role)

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await service.revoke_invitation(uuid.uuid4())

    async def test_revoke_invitation_already_accepted(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        basic_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test revoking already-accepted invitation fails."""
        from tracecat.invitations.service import accept_invitation_for_user

        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email=basic_user.email,
            role_id=rbac_roles["workspace-editor"],
        )

        # Accept the invitation
        await accept_invitation_for_user(
            session, user_id=basic_user.id, token=invitation.token
        )

        # Try to revoke
        with pytest.raises(
            TracecatValidationError, match="Cannot revoke invitation with status"
        ):
            await service.revoke_invitation(invitation.id)

    async def test_revoke_invitation_already_revoked(
        self,
        session: AsyncSession,
        inv_org: Organization,
        inv_workspace: Workspace,
        admin_user: User,
        rbac_roles: dict[str, uuid.UUID],
    ):
        """Test revoking already-revoked invitation fails."""
        role = create_workspace_admin_role(inv_org.id, inv_workspace.id, admin_user.id)
        service = InvitationService(session, role=role)

        invitation = await service._create_email_invitation(
            inv_workspace.id,
            email="revoke-twice@example.com",
            role_id=rbac_roles["workspace-editor"],
        )

        # Revoke once
        await service.revoke_invitation(invitation.id)

        # Try to revoke again
        with pytest.raises(
            TracecatValidationError, match="Cannot revoke invitation with status"
        ):
            await service.revoke_invitation(invitation.id)


class TestTokenGeneration:
    """Tests for invitation token generation."""

    def test_generate_token_length(self):
        """Test that generated token has correct length."""
        from tracecat.invitations.service import _generate_invitation_token

        token = _generate_invitation_token()
        assert len(token) == 64

    def test_generate_token_unique(self):
        """Test that generated tokens are unique."""
        from tracecat.invitations.service import _generate_invitation_token

        tokens = {_generate_invitation_token() for _ in range(100)}
        assert len(tokens) == 100  # All unique
