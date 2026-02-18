"""Tests for OrganizationMembership model and related functionality."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.credentials import get_role_from_user
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    User,
)


class TestOrganizationMembershipModel:
    """Tests for the OrganizationMembership model."""

    @pytest.mark.anyio
    async def test_create_organization_membership(self, session: AsyncSession):
        """Test creating an OrganizationMembership record."""
        # Create organization
        org = Organization(
            id=uuid.uuid4(),
            name="Test Organization",
            slug=f"test-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        # Create user
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        # Create organization membership
        membership = OrganizationMembership(
            user_id=user.id,
            organization_id=org.id,
        )
        session.add(membership)
        await session.commit()

        # Verify membership was created
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        fetched = result.scalar_one()
        assert fetched.user_id == user.id
        assert fetched.organization_id == org.id
        assert fetched.created_at is not None
        assert fetched.updated_at is not None

    @pytest.mark.anyio
    async def test_organization_membership_with_admin_user(self, session: AsyncSession):
        """Test creating membership for an admin user."""
        org = Organization(
            id=uuid.uuid4(),
            name="Admin Test Org",
            slug=f"admin-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

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
            organization_id=org.id,
        )
        session.add(membership)
        await session.commit()

        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
            )
        )
        fetched = result.scalar_one()
        assert fetched.user_id == user.id
        assert fetched.organization_id == org.id

    @pytest.mark.anyio
    async def test_organization_membership_cascade_delete_user(
        self, session: AsyncSession
    ):
        """Test that membership is deleted when user is deleted."""
        org = Organization(
            id=uuid.uuid4(),
            name="Cascade Test Org",
            slug=f"cascade-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        user = User(
            id=uuid.uuid4(),
            email=f"cascade-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        user_id = user.id

        membership = OrganizationMembership(
            user_id=user.id,
            organization_id=org.id,
        )
        session.add(membership)
        await session.commit()

        # Delete user
        await session.delete(user)
        await session.commit()

        # Verify membership was also deleted
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_organization_membership_cascade_delete_org(
        self, session: AsyncSession
    ):
        """Test that membership is deleted when organization is deleted."""
        org = Organization(
            id=uuid.uuid4(),
            name="Cascade Org Test",
            slug=f"cascade-org2-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)
        org_id = org.id

        user = User(
            id=uuid.uuid4(),
            email=f"cascade2-{uuid.uuid4().hex[:8]}@example.com",
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
            organization_id=org.id,
        )
        session.add(membership)
        await session.commit()

        # Delete organization
        await session.delete(org)
        await session.commit()

        # Verify membership was also deleted
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
            )
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_user_multiple_organization_memberships(self, session: AsyncSession):
        """Test user can be member of multiple organizations."""
        org1 = Organization(
            id=uuid.uuid4(),
            name="Multi Org 1",
            slug=f"multi-org1-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        org2 = Organization(
            id=uuid.uuid4(),
            name="Multi Org 2",
            slug=f"multi-org2-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add_all([org1, org2])

        user = User(
            id=uuid.uuid4(),
            email=f"multi-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        membership1 = OrganizationMembership(
            user_id=user.id,
            organization_id=org1.id,
        )
        membership2 = OrganizationMembership(
            user_id=user.id,
            organization_id=org2.id,
        )
        session.add_all([membership1, membership2])
        await session.commit()

        # Verify both memberships exist
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
            )
        )
        memberships = result.scalars().all()
        assert len(memberships) == 2
        org_ids = {m.organization_id for m in memberships}
        assert org_ids == {org1.id, org2.id}


class TestRoleCreation:
    """Tests for Role class creation."""

    def test_role_with_workspace(self):
        """Test Role can be created with workspace_id."""
        role = Role(
            type="user",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        assert role.type == "user"
        assert role.workspace_id is not None

    def test_role_without_workspace(self):
        """Test Role can be created without workspace_id (defaults to None)."""
        role = Role(
            type="user",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            service_id="tracecat-api",
        )
        assert role.workspace_id is None


class TestGetRoleFromUser:
    """Tests for get_role_from_user function."""

    @pytest.mark.anyio
    async def test_get_role_from_user_basic(self, session: AsyncSession):
        """Test get_role_from_user returns correct role."""
        user = User(
            id=uuid.uuid4(),
            email=f"role-test-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.commit()

        role = get_role_from_user(
            user=user,
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        )

        assert role.type == "user"
        assert role.user_id == user.id

    @pytest.mark.anyio
    async def test_get_role_from_user_without_workspace(self, session: AsyncSession):
        """Test get_role_from_user without workspace_id defaults to None."""
        user = User(
            id=uuid.uuid4(),
            email=f"role-test2-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.commit()

        role = get_role_from_user(
            user=user,
            organization_id=uuid.uuid4(),
        )

        assert role.workspace_id is None

    @pytest.mark.anyio
    async def test_get_role_from_superuser(self, session: AsyncSession):
        """Test get_role_from_user for superuser sets is_platform_superuser."""
        user = User(
            id=uuid.uuid4(),
            email=f"superuser-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        session.add(user)
        await session.commit()

        role = get_role_from_user(
            user=user,
            organization_id=uuid.uuid4(),
        )

        assert role.is_platform_superuser is True


class TestOrganizationMembershipRelationships:
    """Tests for User and Organization relationships via OrganizationMembership."""

    @pytest.mark.anyio
    async def test_user_organizations_relationship(self, session: AsyncSession):
        """Test User.organizations relationship returns correct organizations."""
        org1 = Organization(
            id=uuid.uuid4(),
            name="Rel Org 1",
            slug=f"rel-org1-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        org2 = Organization(
            id=uuid.uuid4(),
            name="Rel Org 2",
            slug=f"rel-org2-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add_all([org1, org2])

        user = User(
            id=uuid.uuid4(),
            email=f"rel-user-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        membership1 = OrganizationMembership(
            user_id=user.id,
            organization_id=org1.id,
        )
        membership2 = OrganizationMembership(
            user_id=user.id,
            organization_id=org2.id,
        )
        session.add_all([membership1, membership2])
        await session.commit()

        # Refresh user to load relationship
        await session.refresh(user, ["organizations"])

        # Verify relationship
        assert len(user.organizations) == 2
        org_ids = {org.id for org in user.organizations}
        assert org_ids == {org1.id, org2.id}

    @pytest.mark.anyio
    async def test_organization_members_relationship(self, session: AsyncSession):
        """Test Organization.members relationship returns correct users."""
        org = Organization(
            id=uuid.uuid4(),
            name="Members Org",
            slug=f"members-org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        session.add(org)

        user1 = User(
            id=uuid.uuid4(),
            email=f"member1-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        user2 = User(
            id=uuid.uuid4(),
            email=f"member2-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.ADMIN,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add_all([user1, user2])
        await session.flush()

        membership1 = OrganizationMembership(
            user_id=user1.id,
            organization_id=org.id,
        )
        membership2 = OrganizationMembership(
            user_id=user2.id,
            organization_id=org.id,
        )
        session.add_all([membership1, membership2])
        await session.commit()

        # Refresh org to load relationship
        await session.refresh(org, ["members"])

        # Verify relationship
        assert len(org.members) == 2
        user_ids = {u.id for u in org.members}
        assert user_ids == {user1.id, user2.id}
