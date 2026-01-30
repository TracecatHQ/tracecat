"""Tests for OrgService with organization-scoped queries."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole
from tracecat.db.models import (
    AccessToken,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    User,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.service import OrgService


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
async def user_in_org1(session: AsyncSession, org1: Organization) -> User:
    """Create a user that belongs to org1."""
    user = User(
        id=uuid.uuid4(),
        email=f"user-org1-{uuid.uuid4().hex[:8]}@example.com",
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
        organization_id=org1.id,
    )
    session.add(membership)
    await session.commit()
    return user


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


def create_admin_role(organization_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    """Create an org admin role for testing (not a platform superuser)."""
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        service_id="tracecat-api",
        is_platform_superuser=False,
    )


def create_superuser_role(organization_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    """Create a platform superuser role for testing."""
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.OWNER,
        service_id="tracecat-api",
        is_platform_superuser=True,
    )


class TestOrganizationServiceListMembers:
    """Tests for OrganizationService.list_members()."""

    @pytest.mark.anyio
    async def test_list_members_returns_only_org_members(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        user_in_org1: User,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test list_members only returns users in the specified organization."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        members = await service.list_members()

        member_ids = {user.id for user, _ in members}
        assert user_in_org1.id in member_ids
        assert admin_in_org1.id in member_ids
        assert user_in_org2.id not in member_ids
        assert len(members) == 2

    @pytest.mark.anyio
    async def test_list_members_empty_org(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
        org2: Organization,
    ):
        """Test list_members returns empty list for org with no additional members."""
        # Query org2 which has no members
        role = create_admin_role(org2.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        members = await service.list_members()

        assert len(members) == 0


class TestOrganizationServiceGetMember:
    """Tests for OrganizationService.get_member()."""

    @pytest.mark.anyio
    async def test_get_member_in_same_org(
        self,
        session: AsyncSession,
        org1: Organization,
        user_in_org1: User,
        admin_in_org1: User,
    ):
        """Test get_member returns user when they're in the same org."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        member, org_role = await service.get_member(user_in_org1.id)

        assert member.id == user_in_org1.id
        assert member.email == user_in_org1.email
        assert org_role == OrgRole.MEMBER

    @pytest.mark.anyio
    async def test_get_member_in_different_org_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test get_member raises NoResultFound for user in different org."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.get_member(user_in_org2.id)

    @pytest.mark.anyio
    async def test_get_member_nonexistent_user_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test get_member raises NoResultFound for nonexistent user."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.get_member(uuid.uuid4())


class TestOrganizationServiceDeleteMember:
    """Tests for OrganizationService.delete_member()."""

    @pytest.mark.anyio
    async def test_delete_member_in_same_organization(
        self,
        session: AsyncSession,
        org1: Organization,
        user_in_org1: User,
        admin_in_org1: User,
    ):
        """Test delete_member removes user when they're in the same organization."""
        user_id = user_in_org1.id

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        await service.delete_member(user_id)

        # Verify user was deleted
        result = await session.execute(select(User).where(User.id == user_id))  # pyright: ignore[reportArgumentType]
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_delete_member_in_different_organization_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test delete_member raises NoResultFound for user in different organization."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.delete_member(user_in_org2.id)

    @pytest.mark.anyio
    async def test_delete_superuser_raises_authorization_error(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test delete_member raises TracecatAuthorizationError for superuser."""
        # Create a superuser in org1
        superuser = User(
            id=uuid.uuid4(),
            email=f"superuser-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.ADMIN,
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        session.add(superuser)
        await session.flush()

        membership = OrganizationMembership(
            user_id=superuser.id,
            organization_id=org1.id,
        )
        session.add(membership)
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(TracecatAuthorizationError, match="Cannot delete superuser"):
            await service.delete_member(superuser.id)

        # Verify superuser was NOT deleted
        result = await session.execute(select(User).where(User.id == superuser.id))  # pyright: ignore[reportArgumentType]
        assert result.scalar_one_or_none() is not None

    @pytest.mark.anyio
    async def test_delete_nonexistent_member_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test delete_member raises NoResultFound for nonexistent user."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.delete_member(uuid.uuid4())


class TestOrganizationServiceDeleteOrganization:
    """Tests for OrgService.delete_organization()."""

    @pytest.mark.anyio
    async def test_owner_can_delete_organization_with_confirmation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ) -> None:
        role = Role(
            type="user",
            user_id=admin_in_org1.id,
            organization_id=org1.id,
            access_level=AccessLevel.ADMIN,
            org_role=OrgRole.OWNER,
            service_id="tracecat-api",
            is_platform_superuser=False,
        )
        service = OrgService(session, role=role)

        await service.delete_organization(confirmation=org1.name)

        org_result = await session.execute(
            select(Organization).where(Organization.id == org1.id)
        )
        membership_result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org1.id
            )
        )
        assert org_result.scalar_one_or_none() is None
        assert membership_result.scalars().all() == []

    @pytest.mark.anyio
    async def test_delete_organization_requires_exact_confirmation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ) -> None:
        role = Role(
            type="user",
            user_id=admin_in_org1.id,
            organization_id=org1.id,
            access_level=AccessLevel.ADMIN,
            org_role=OrgRole.OWNER,
            service_id="tracecat-api",
            is_platform_superuser=False,
        )
        service = OrgService(session, role=role)

        with pytest.raises(
            TracecatValidationError,
            match="Confirmation text must exactly match the organization name.",
        ):
            await service.delete_organization(confirmation="wrong")

        org_result = await session.execute(
            select(Organization).where(Organization.id == org1.id)
        )
        assert org_result.scalar_one_or_none() is not None

    @pytest.mark.anyio
    async def test_admin_cannot_delete_organization(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ) -> None:
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="required org role",
        ):
            await service.delete_organization(confirmation=org1.name)


class TestOrganizationServiceSessions:
    """Tests for OrganizationService session management."""

    @pytest.mark.anyio
    async def test_list_sessions_returns_only_org_sessions(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        user_in_org1: User,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test list_sessions only returns sessions for users in the org."""
        # Create sessions for users in both orgs
        token1 = AccessToken(
            token=f"token-{uuid.uuid4().hex}",
            user_id=user_in_org1.id,
        )
        token2 = AccessToken(
            token=f"token-{uuid.uuid4().hex}",
            user_id=admin_in_org1.id,
        )
        token3 = AccessToken(
            token=f"token-{uuid.uuid4().hex}",
            user_id=user_in_org2.id,
        )
        session.add_all([token1, token2, token3])
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        sessions = await service.list_sessions()

        session_user_ids = {s.user_id for s in sessions}
        assert user_in_org1.id in session_user_ids
        assert admin_in_org1.id in session_user_ids
        assert user_in_org2.id not in session_user_ids
        assert len(sessions) == 2

    @pytest.mark.anyio
    async def test_delete_session_in_same_org(
        self,
        session: AsyncSession,
        org1: Organization,
        user_in_org1: User,
        admin_in_org1: User,
    ):
        """Test delete_session works for session belonging to user in same org."""
        token = AccessToken(
            token=f"token-{uuid.uuid4().hex}",
            user_id=user_in_org1.id,
        )
        session.add(token)
        await session.commit()
        token_id = token.id

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        await service.delete_session(token_id)

        # Verify session was deleted
        result = await session.execute(
            select(AccessToken).where(AccessToken.id == token_id)
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_delete_session_in_different_org_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test delete_session raises NoResultFound for session in different org."""
        token = AccessToken(
            token=f"token-{uuid.uuid4().hex}",
            user_id=user_in_org2.id,
        )
        session.add(token)
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.delete_session(token.id)


class TestOrganizationServiceAddMember:
    """Tests for OrgService.add_member()."""

    @pytest.mark.anyio
    async def test_add_member_creates_membership(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test add_member creates an OrganizationMembership record."""
        # Create a new user not yet in the org
        new_user = User(
            id=uuid.uuid4(),
            email=f"newuser-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(new_user)
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        membership = await service.add_member(
            user_id=new_user.id,
            organization_id=org1.id,
            role=OrgRole.MEMBER,
        )

        assert membership.user_id == new_user.id
        assert membership.organization_id == org1.id

    @pytest.mark.anyio
    async def test_add_member_with_admin_role(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test add_member can assign admin role."""
        new_user = User(
            id=uuid.uuid4(),
            email=f"newadmin-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.ADMIN,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(new_user)
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        membership = await service.add_member(
            user_id=new_user.id,
            organization_id=org1.id,
            role=OrgRole.ADMIN,
        )

        assert membership.user_id == new_user.id
        assert membership.organization_id == org1.id

    @pytest.mark.anyio
    async def test_add_member_with_owner_role(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test add_member can assign owner role."""
        new_user = User(
            id=uuid.uuid4(),
            email=f"newowner-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.ADMIN,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(new_user)
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        membership = await service.add_member(
            user_id=new_user.id,
            organization_id=org1.id,
            role=OrgRole.OWNER,
        )

        assert membership.user_id == new_user.id
        assert membership.organization_id == org1.id

    @pytest.mark.anyio
    async def test_add_member_default_role_is_member(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test add_member defaults to MEMBER role when not specified."""
        new_user = User(
            id=uuid.uuid4(),
            email=f"defaultrole-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(new_user)
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        membership = await service.add_member(
            user_id=new_user.id,
            organization_id=org1.id,
        )

        assert membership.user_id == new_user.id
        assert membership.organization_id == org1.id

    @pytest.mark.anyio
    async def test_add_member_user_appears_in_list_members(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test that added member appears in list_members."""
        new_user = User(
            id=uuid.uuid4(),
            email=f"listcheck-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(new_user)
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        # Add member
        await service.add_member(
            user_id=new_user.id,
            organization_id=org1.id,
        )

        # Verify they appear in list_members
        members = await service.list_members()
        member_ids = {user.id for user, _ in members}
        assert new_user.id in member_ids


class TestOrganizationServiceInvitations:
    """Tests for OrgService invitation methods."""

    @pytest.mark.anyio
    async def test_create_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test create_invitation creates an invitation record."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(
            email="newuser@example.com",
            role_slug="member",
        )

        assert invitation.email == "newuser@example.com"
        assert invitation.role.slug == "member"
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
    ):
        """Test create_invitation can assign admin role."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(
            email="newadmin@example.com",
            role_slug="admin",
        )

        assert invitation.role.slug == "admin"

    @pytest.mark.anyio
    async def test_create_owner_invitation_requires_owner_or_superuser(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test that org admins cannot create OWNER invitations."""
        # Org admin (not superuser) should not be able to create OWNER invitations
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="Only organization owners can create owner invitations",
        ):
            await service.create_invitation(
                email="newowner@example.com",
                role_slug="owner",
            )

    @pytest.mark.anyio
    async def test_superuser_can_create_owner_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test that platform superusers can create OWNER invitations."""
        role = create_superuser_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(
            email="newowner@example.com",
            role_slug="owner",
        )

        assert invitation.role.slug == "owner"
        assert invitation.email == "newowner@example.com"

    @pytest.mark.anyio
    async def test_create_invitation_duplicate_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test create_invitation raises error for duplicate email in same org."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        await service.create_invitation(email="duplicate@example.com")

        with pytest.raises(
            TracecatValidationError,
            match="An invitation already exists for duplicate@example.com",
        ):
            await service.create_invitation(email="duplicate@example.com")

    @pytest.mark.anyio
    async def test_create_invitation_replaces_expired_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test create_invitation replaces an expired invitation."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        # Create first invitation
        invitation = await service.create_invitation(email="expired@example.com")
        old_id = invitation.id

        # Manually expire it
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        # Create new invitation for same email - should succeed
        new_invitation = await service.create_invitation(email="expired@example.com")

        assert new_invitation.id != old_id
        assert new_invitation.email == "expired@example.com"
        assert new_invitation.expires_at > datetime.now(UTC)

    @pytest.mark.anyio
    async def test_create_invitation_existing_member_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test create_invitation raises error when email is already a member."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        # admin_in_org1 is already a member of org1, try to invite their email
        with pytest.raises(
            TracecatValidationError,
            match="is already a member of this organization",
        ):
            await service.create_invitation(email=admin_in_org1.email)

    @pytest.mark.anyio
    async def test_list_invitations_returns_org_invitations(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
    ):
        """Test list_invitations only returns invitations for the organization."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        # Create invitations for org1
        inv1 = await service.create_invitation(email="user1@example.com")
        inv2 = await service.create_invitation(email="user2@example.com")

        # Create invitation for org2 directly
        # First, create a role for org2
        from tracecat.db.models import Role as RoleModel

        org2_role = RoleModel(
            id=uuid.uuid4(),
            name="Member",
            slug="member",
            organization_id=org2.id,
        )
        session.add(org2_role)
        await session.flush()

        org2_invitation = OrganizationInvitation(
            organization_id=org2.id,
            email="org2user@example.com",
            role_id=org2_role.id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            status=InvitationStatus.PENDING,
        )
        session.add(org2_invitation)
        await session.commit()

        # List invitations for org1
        invitations = await service.list_invitations()

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
    ):
        """Test list_invitations can filter by status."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        # Create pending invitation
        pending_inv = await service.create_invitation(email="pending@example.com")

        # Create and revoke another invitation
        revoked_inv = await service.create_invitation(email="revoked@example.com")
        await service.revoke_invitation(revoked_inv.id)

        # List only pending invitations
        pending_invitations = await service.list_invitations(
            status=InvitationStatus.PENDING
        )
        assert len(pending_invitations) == 1
        assert pending_invitations[0].id == pending_inv.id

        # List only revoked invitations
        revoked_invitations = await service.list_invitations(
            status=InvitationStatus.REVOKED
        )
        assert len(revoked_invitations) == 1
        assert revoked_invitations[0].id == revoked_inv.id

    @pytest.mark.anyio
    async def test_get_invitation_by_token(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test get_invitation_by_token retrieves invitation."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(email="test@example.com")

        retrieved = await service.get_invitation_by_token(invitation.token)

        assert retrieved.id == invitation.id
        assert retrieved.email == invitation.email

    @pytest.mark.anyio
    async def test_get_invitation_by_token_returns_org_and_inviter_info(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test get_invitation_by_token returns org name and inviter info for acceptance page."""
        # Set inviter's name for test
        admin_in_org1.first_name = "John"
        admin_in_org1.last_name = "Doe"
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(email="test@example.com")

        # Get invitation by token
        retrieved = await service.get_invitation_by_token(invitation.token)

        # Verify organization info is available
        assert retrieved.organization_id == org1.id

        # Fetch org to verify name can be resolved
        result = await session.execute(
            select(Organization).where(Organization.id == retrieved.organization_id)
        )
        org = result.scalar_one()
        assert org.name == "Test Organization 1"

        # Verify inviter info is available
        assert retrieved.invited_by == admin_in_org1.id
        result = await session.execute(
            select(User).where(User.id == retrieved.invited_by)  # pyright: ignore[reportArgumentType]
        )
        inviter = result.scalar_one()
        assert inviter.first_name == "John"
        assert inviter.last_name == "Doe"
        assert inviter.email == admin_in_org1.email

    @pytest.mark.anyio
    async def test_get_invitation_by_token_not_found(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test get_invitation_by_token raises error for invalid token."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(TracecatNotFoundError, match="Invitation not found"):
            await service.get_invitation_by_token("invalid-token")

    @pytest.mark.anyio
    async def test_revoke_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test revoke_invitation marks invitation as revoked."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(email="test@example.com")
        assert invitation.status == InvitationStatus.PENDING

        revoked = await service.revoke_invitation(invitation.id)

        assert revoked.status == InvitationStatus.REVOKED

    @pytest.mark.anyio
    async def test_revoke_invitation_already_revoked_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        admin_in_org1: User,
    ):
        """Test revoke_invitation raises error for already revoked invitation."""
        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        invitation = await service.create_invitation(email="test@example.com")
        await service.revoke_invitation(invitation.id)

        with pytest.raises(
            TracecatAuthorizationError, match="Cannot revoke invitation"
        ):
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
        # Create invitation in org2 directly
        # First, create a role for org2
        from tracecat.db.models import Role as RoleModel

        org2_role = RoleModel(
            id=uuid.uuid4(),
            name="Member",
            slug="member",
            organization_id=org2.id,
        )
        session.add(org2_role)
        await session.flush()

        org2_invitation = OrganizationInvitation(
            organization_id=org2.id,
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
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.revoke_invitation(org2_invitation.id)

    @pytest.mark.anyio
    async def test_accept_invitation(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test accept_invitation creates membership."""
        # Create invitation as admin
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        admin_service = OrgService(session, role=admin_role)
        invitation = await admin_service.create_invitation(
            email=user_in_org2.email,
            role_slug="member",
        )

        # Accept as user_in_org2
        user_role = Role(
            type="user",
            user_id=user_in_org2.id,
            organization_id=org2.id,
            access_level=AccessLevel.BASIC,
            service_id="tracecat-api",
        )
        user_service = OrgService(session, role=user_role)
        membership = await user_service.accept_invitation(invitation.token)

        assert membership.user_id == user_in_org2.id
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
    ):
        """Test accept_invitation raises error for already accepted invitation."""
        # Create and accept invitation
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        admin_service = OrgService(session, role=admin_role)
        invitation = await admin_service.create_invitation(
            email=user_in_org2.email,
            role_slug="member",
        )

        user_role = Role(
            type="user",
            user_id=user_in_org2.id,
            organization_id=org2.id,
            access_level=AccessLevel.BASIC,
            service_id="tracecat-api",
        )
        user_service = OrgService(session, role=user_role)
        await user_service.accept_invitation(invitation.token)

        # Try to accept again with the same user
        with pytest.raises(TracecatAuthorizationError, match="already been accepted"):
            await user_service.accept_invitation(invitation.token)

    @pytest.mark.anyio
    async def test_accept_invitation_email_mismatch_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test accept_invitation raises error when user email doesn't match invitation."""
        # Create invitation for user_in_org2
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        admin_service = OrgService(session, role=admin_role)
        invitation = await admin_service.create_invitation(
            email=user_in_org2.email,
            role_slug="member",
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
        different_role = Role(
            type="user",
            user_id=different_user.id,
            organization_id=org2.id,
            access_level=AccessLevel.BASIC,
            service_id="tracecat-api",
        )
        different_service = OrgService(session, role=different_role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="invitation was sent to a different email address",
        ):
            await different_service.accept_invitation(invitation.token)

    @pytest.mark.anyio
    async def test_accept_invitation_revoked_raises(
        self,
        session: AsyncSession,
        org1: Organization,
        org2: Organization,
        admin_in_org1: User,
        user_in_org2: User,
    ):
        """Test accept_invitation raises error for revoked invitation."""
        # Create and revoke invitation
        admin_role = create_admin_role(org1.id, admin_in_org1.id)
        admin_service = OrgService(session, role=admin_role)
        invitation = await admin_service.create_invitation(
            email=user_in_org2.email,
            role_slug="member",
        )
        await admin_service.revoke_invitation(invitation.id)

        # Try to accept
        user_role = Role(
            type="user",
            user_id=user_in_org2.id,
            organization_id=org2.id,
            access_level=AccessLevel.BASIC,
            service_id="tracecat-api",
        )
        user_service = OrgService(session, role=user_role)

        with pytest.raises(TracecatAuthorizationError, match="has been revoked"):
            await user_service.accept_invitation(invitation.token)
