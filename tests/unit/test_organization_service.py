"""Tests for OrgService with organization-scoped queries."""

import uuid

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
    OrganizationMembership,
    User,
)
from tracecat.exceptions import TracecatAuthorizationError
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
    await session.flush()
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
    await session.flush()
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
        role=OrgRole.MEMBER,
    )
    session.add(membership)
    await session.flush()
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
        role=OrgRole.ADMIN,
    )
    session.add(membership)
    await session.flush()
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
        role=OrgRole.MEMBER,
    )
    session.add(membership)
    await session.flush()
    return user


def create_admin_role(organization_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    """Create an admin role for testing."""
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-api",
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
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        members = await service.list_members()

        member_ids = {m.id for m in members}
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
        await session.commit()

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
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        member = await service.get_member(user_in_org1.id)

        assert member.id == user_in_org1.id
        assert member.email == user_in_org1.email

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
        await session.commit()

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
        await session.commit()

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
        await session.commit()
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
        await session.commit()

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
            role=OrgRole.OWNER,
        )
        session.add(membership)
        await session.commit()

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
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.delete_member(uuid.uuid4())


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
        await session.commit()

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
        await session.commit()

        role = create_admin_role(org1.id, admin_in_org1.id)
        service = OrgService(session, role=role)

        with pytest.raises(NoResultFound):
            await service.delete_session(token.id)
