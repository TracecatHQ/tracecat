"""Tests for InvitationService org-level invitation methods."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.db.models import (
    Invitation,
    Organization,
    OrganizationMembership,
    User,
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
from tracecat.invitations.service import (
    InvitationService,
    accept_invitation_for_user,
)


@pytest.fixture
async def org1(session: AsyncSession) -> Organization:
    """Create first test organization."""
    org = Organization(
        id=uuid.uuid4(),
        name="Test Organization 1",
        slug=f"test-org1-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def org2(session: AsyncSession) -> Organization:
    """Create second test organization."""
    org = Organization(
        id=uuid.uuid4(),
        name="Test Organization 2",
        slug=f"test-org2-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def admin_in_org1(session: AsyncSession, org1: Organization) -> User:
    """Create an admin user that belongs to org1."""
    user = User(
        id=uuid.uuid4(),
        email=f"admin-org1-{uuid.uuid4().hex[:8]}@example.com",
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
        organization_id=org1.id,
    )
    session.add(membership)
    await session.commit()
    return user


@pytest.fixture
async def user_in_org2(session: AsyncSession, org2: Organization) -> User:
    """Create a user that belongs to org2."""
    user = User(
        id=uuid.uuid4(),
        email=f"user-org2-{uuid.uuid4().hex[:8]}@example.com",
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
        organization_id=org2.id,
    )
    session.add(membership)
    await session.commit()
    return user


@pytest.fixture
async def org1_member_role(session: AsyncSession, org1: Organization) -> DBRole:
    """Create an RBAC 'organization-member' role for org1."""
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        description="Default member role",
        organization_id=org1.id,
    )
    session.add(role)
    await session.commit()
    return role


@pytest.fixture
async def org1_admin_role(session: AsyncSession, org1: Organization) -> DBRole:
    """Create an RBAC 'organization-admin' role for org1."""
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Admin",
        slug="organization-admin",
        description="Admin role",
        organization_id=org1.id,
    )
    session.add(role)
    await session.commit()
    return role


@pytest.fixture
async def org1_owner_role(session: AsyncSession, org1: Organization) -> DBRole:
    """Create an RBAC 'organization-owner' role for org1."""
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Owner",
        slug="organization-owner",
        description="Owner role",
        organization_id=org1.id,
    )
    session.add(role)
    await session.commit()
    return role


def create_admin_role(organization_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    """Create an org admin role for testing (not a platform superuser)."""
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        is_platform_superuser=False,
        scopes=ORG_ADMIN_SCOPES,
    )


def create_superuser_role(organization_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    """Create a platform superuser role for testing."""
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        is_platform_superuser=True,
        scopes=ORG_ADMIN_SCOPES,
    )


class TestOrgInvitationService:
    """Tests for InvitationService org-level invitation methods."""

    @pytest.mark.anyio
    async def test_create_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test create_org_invitation creates an invitation record."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        invitation = await service.create_org_invitation(
            email="newuser@example.com",
            role_id=org1_member_role.id,
        )

        assert invitation.email == "newuser@example.com"
        assert invitation.role_id == org1_member_role.id
        assert invitation.organization_id == org1.id
        assert invitation.invited_by == admin_in_org1.id
        assert invitation.status == InvitationStatus.PENDING
        assert invitation.token is not None
        assert len(invitation.token) > 0

    @pytest.mark.anyio
    async def test_create_invitation_with_admin_role(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_admin_role: DBRole,
    ):
        """Test create_org_invitation can assign admin role."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        invitation = await service.create_org_invitation(
            email="newadmin@example.com",
            role_id=org1_admin_role.id,
        )

        assert invitation.role_id == org1_admin_role.id

    @pytest.mark.anyio
    async def test_create_owner_invitation_requires_owner_or_superuser(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_owner_role: DBRole,
    ):
        """Test that org admins cannot create OWNER invitations."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="Only organization owners can create owner invitations",
        ):
            await service.create_org_invitation(
                email="newowner@example.com",
                role_id=org1_owner_role.id,
            )

    @pytest.mark.anyio
    async def test_superuser_can_create_owner_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_owner_role: DBRole,
    ):
        """Test that platform superusers can create OWNER invitations."""
        role = create_superuser_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        invitation = await service.create_org_invitation(
            email="newowner@example.com",
            role_id=org1_owner_role.id,
        )

        assert invitation.role_id == org1_owner_role.id
        assert invitation.email == "newowner@example.com"

    @pytest.mark.anyio
    async def test_create_invitation_duplicate_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test create_org_invitation raises error for duplicate email in same org."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        await service.create_org_invitation(
            email="duplicate@example.com", role_id=org1_member_role.id
        )

        with pytest.raises(
            TracecatValidationError,
            match="An invitation already exists for duplicate@example.com",
        ):
            await service.create_org_invitation(
                email="duplicate@example.com", role_id=org1_member_role.id
            )

    @pytest.mark.anyio
    async def test_create_invitation_replaces_expired_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test create_org_invitation replaces an expired invitation."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        # Create first invitation
        invitation = await service.create_org_invitation(
            email="expired@example.com", role_id=org1_member_role.id
        )
        old_id = invitation.id

        # Manually expire it
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        # Create new invitation for same email - should succeed
        new_invitation = await service.create_org_invitation(
            email="expired@example.com", role_id=org1_member_role.id
        )

        assert new_invitation.id != old_id
        assert new_invitation.email == "expired@example.com"
        assert new_invitation.expires_at > datetime.now(UTC)

    @pytest.mark.anyio
    async def test_create_invitation_existing_member_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test create_org_invitation raises error when email is already a member."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        # admin_in_org1 is already a member of org1, try to invite their email
        with pytest.raises(
            TracecatValidationError,
            match="is already a member of this organization",
        ):
            await service.create_org_invitation(
                email=admin_in_org1.email, role_id=org1_member_role.id
            )

    @pytest.mark.anyio
    async def test_list_invitations_returns_org_invitations(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test list_org_invitations only returns invitations for the organization."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        # Create invitations for org1
        inv1 = await service.create_org_invitation(
            email="user1@example.com", role_id=org1_member_role.id
        )
        inv2 = await service.create_org_invitation(
            email="user2@example.com", role_id=org1_member_role.id
        )

        # Create invitation for org2 directly (need a role for org2)
        org2_role = DBRole(
            id=uuid.uuid4(),
            name="Organization Member",
            slug="organization-member",
            description="Default member role",
            organization_id=org2.id,
        )
        session.add(org2_role)
        await session.flush()
        org2_invitation = Invitation(
            organization_id=org2.id,
            workspace_id=None,
            email="org2user@example.com",
            role_id=org2_role.id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            status=InvitationStatus.PENDING,
        )
        session.add(org2_invitation)
        await session.commit()

        # List invitations for org1
        invitations = await service.list_org_invitations()

        invitation_ids = {inv.id for inv in invitations}
        assert inv1.id in invitation_ids
        assert inv2.id in invitation_ids
        assert org2_invitation.id not in invitation_ids
        assert len(invitations) == 2

    @pytest.mark.anyio
    async def test_list_invitations_filter_by_status(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test list_org_invitations can filter by status."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        # Create pending invitation
        pending_inv = await service.create_org_invitation(
            email="pending@example.com", role_id=org1_member_role.id
        )

        # Create and revoke another invitation
        revoked_inv = await service.create_org_invitation(
            email="revoked@example.com", role_id=org1_member_role.id
        )
        await service.revoke_invitation(revoked_inv.id)

        # List only pending invitations
        pending_invitations = await service.list_org_invitations(
            status=InvitationStatus.PENDING
        )
        assert len(pending_invitations) == 1
        assert pending_invitations[0].id == pending_inv.id

        # List only revoked invitations
        revoked_invitations = await service.list_org_invitations(
            status=InvitationStatus.REVOKED
        )
        assert len(revoked_invitations) == 1
        assert revoked_invitations[0].id == revoked_inv.id

    @pytest.mark.anyio
    async def test_get_invitation_by_id(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test get_invitation retrieves invitation by ID."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        invitation = await service.create_org_invitation(
            email="test@example.com", role_id=org1_member_role.id
        )

        retrieved = await service.get_invitation(invitation.id)

        assert retrieved.id == invitation.id
        assert retrieved.email == invitation.email

    @pytest.mark.anyio
    async def test_get_invitation_not_found(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test get_invitation raises error for non-existent ID."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await service.get_invitation(uuid.uuid4())

    @pytest.mark.anyio
    async def test_revoke_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test revoke_invitation marks invitation as revoked."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        invitation = await service.create_org_invitation(
            email="test@example.com", role_id=org1_member_role.id
        )
        assert invitation.status == InvitationStatus.PENDING

        revoked = await service.revoke_invitation(invitation.id)

        assert revoked.status == InvitationStatus.REVOKED

    @pytest.mark.anyio
    async def test_revoke_invitation_already_revoked_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org1_member_role: DBRole,
    ):
        """Test revoke_invitation raises error for already revoked invitation."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        invitation = await service.create_org_invitation(
            email="test@example.com", role_id=org1_member_role.id
        )
        await service.revoke_invitation(invitation.id)

        with pytest.raises(TracecatValidationError, match="Cannot revoke invitation"):
            await service.revoke_invitation(invitation.id)

    @pytest.mark.anyio
    async def test_revoke_invitation_from_different_org_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
    ):
        """Test revoke_invitation raises error for invitation in different org."""
        # Create invitation in org2 directly (need a role for org2)
        org2_role = DBRole(
            id=uuid.uuid4(),
            name="Organization Member",
            slug="organization-member",
            description="Default member role",
            organization_id=org2.id,
        )
        session.add(org2_role)
        await session.flush()
        org2_invitation = Invitation(
            organization_id=org2.id,
            workspace_id=None,
            email="org2user@example.com",
            role_id=org2_role.id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            status=InvitationStatus.PENDING,
        )
        session.add(org2_invitation)
        await session.commit()

        # Try to revoke from org1
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=role)

        with pytest.raises(TracecatNotFoundError):
            await service.revoke_invitation(org2_invitation.id)

    @pytest.mark.anyio
    async def test_accept_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
        org1_member_role: DBRole,
    ):
        """Test accept_invitation_for_user creates membership."""
        # Create invitation as admin
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=admin_role)
        invitation = await service.create_org_invitation(
            email=user_in_org2.email,
            role_id=org1_member_role.id,
        )

        # Accept as user_in_org2
        membership = await accept_invitation_for_user(
            session, user_id=user_in_org2.id, token=invitation.token
        )

        assert membership.user_id == user_in_org2.id
        assert isinstance(membership, OrganizationMembership)
        assert membership.organization_id == org1.id

        # Verify invitation is marked as accepted
        await session.refresh(invitation)
        assert invitation.status == InvitationStatus.ACCEPTED
        assert invitation.accepted_at is not None

    @pytest.mark.anyio
    async def test_accept_invitation_already_accepted_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
        org1_member_role: DBRole,
    ):
        """Test accept raises error for already accepted invitation."""
        # Create and accept invitation
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=admin_role)
        invitation = await service.create_org_invitation(
            email=user_in_org2.email,
            role_id=org1_member_role.id,
        )

        await accept_invitation_for_user(
            session, user_id=user_in_org2.id, token=invitation.token
        )

        # Try to accept again with the same user
        with pytest.raises(TracecatAuthorizationError, match="already been accepted"):
            await accept_invitation_for_user(
                session, user_id=user_in_org2.id, token=invitation.token
            )

    @pytest.mark.anyio
    async def test_accept_invitation_email_mismatch_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
        org1_member_role: DBRole,
    ):
        """Test accept raises error when user email doesn't match invitation."""
        # Create invitation for user_in_org2
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=admin_role)
        invitation = await service.create_org_invitation(
            email=user_in_org2.email,
            role_id=org1_member_role.id,
        )

        # Create a different user with different email
        different_user = User(
            id=uuid.uuid4(),
            email=f"different-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(different_user)
        await session.commit()

        # Try to accept with different user (email mismatch)
        with pytest.raises(
            TracecatAuthorizationError,
            match="invitation was sent to a different email address",
        ):
            await accept_invitation_for_user(
                session, user_id=different_user.id, token=invitation.token
            )

    @pytest.mark.anyio
    async def test_accept_invitation_revoked_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
        org1_member_role: DBRole,
    ):
        """Test accept raises error for revoked invitation."""
        # Create and revoke invitation
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        service = InvitationService(session, role=admin_role)
        invitation = await service.create_org_invitation(
            email=user_in_org2.email,
            role_id=org1_member_role.id,
        )
        await service.revoke_invitation(invitation.id)

        # Try to accept
        with pytest.raises(TracecatAuthorizationError, match="has been revoked"):
            await accept_invitation_for_user(
                session, user_id=user_in_org2.id, token=invitation.token
            )
