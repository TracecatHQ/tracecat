"""Unit tests for RBAC service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.rbac.service import RBACService

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole, ScopeSource
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    Scope,
    User,
    Workspace,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    """Create a test organization."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def user(session: AsyncSession, org: Organization) -> User:
    """Create a test user with org membership."""
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()

    # Add org membership
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    """Create a test workspace."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def seeded_scopes(session: AsyncSession) -> list[Scope]:
    """Seed system scopes and return them."""
    await seed_system_scopes(session)
    result = await session.execute(
        select(Scope).where(Scope.source == ScopeSource.SYSTEM)
    )
    return list(result.scalars().all())


@pytest.fixture
def role(org: Organization, user: User) -> Role:
    """Create a test role for the service."""
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        service_id="tracecat-api",
    )


@pytest.mark.anyio
class TestRBACServiceScopes:
    """Test scope management in RBAC service."""

    async def test_list_scopes_with_system_scopes(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """List scopes should include system scopes."""
        service = RBACService(session, role=role)
        scopes = await service.list_scopes(include_system=True)
        assert len(scopes) > 0
        # All system scopes should be included
        system_scope_names = {s.name for s in seeded_scopes}
        returned_names = {s.name for s in scopes}
        assert system_scope_names.issubset(returned_names)

    async def test_list_scopes_filter_by_source(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """List scopes can filter by source."""
        service = RBACService(session, role=role)
        scopes = await service.list_scopes(
            include_system=True, source=ScopeSource.SYSTEM
        )
        assert all(s.source == ScopeSource.SYSTEM for s in scopes)

    async def test_create_custom_scope(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create a custom scope."""
        service = RBACService(session, role=role)
        scope = await service.create_scope(
            name="custom:test",
            description="A test custom scope",
        )
        assert scope.name == "custom:test"
        assert scope.resource == "custom"
        assert scope.action == "test"
        assert scope.source == ScopeSource.CUSTOM
        assert scope.organization_id == org.id

    async def test_create_scope_invalid_format(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Creating scope with invalid format should fail."""
        service = RBACService(session, role=role)
        with pytest.raises(TracecatValidationError):
            await service.create_scope(name="INVALID SCOPE")

    async def test_delete_custom_scope(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Delete a custom scope."""
        service = RBACService(session, role=role)
        scope = await service.create_scope(name="custom:delete-me")
        await service.delete_scope(scope.id)

        with pytest.raises(TracecatNotFoundError):
            await service.get_scope(scope.id)

    async def test_delete_system_scope_fails(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """Cannot delete system scopes."""
        service = RBACService(session, role=role)
        system_scope = seeded_scopes[0]

        with pytest.raises(TracecatAuthorizationError):
            await service.delete_scope(system_scope.id)


@pytest.mark.anyio
class TestRBACServiceRoles:
    """Test role management in RBAC service."""

    async def test_create_custom_role(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create a custom role."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="Security Analyst",
            description="A custom security analyst role",
        )
        assert custom_role.name == "Security Analyst"
        assert custom_role.organization_id == org.id
        assert custom_role.created_by == role.user_id

    async def test_create_role_with_scopes(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """Create a role with scopes assigned."""
        service = RBACService(session, role=role)
        scope_ids = [s.id for s in seeded_scopes[:3]]

        custom_role = await service.create_role(
            name="Custom Role With Scopes",
            scope_ids=scope_ids,
        )
        assert len(custom_role.scopes) == 3

    async def test_update_role(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Update a custom role."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Original Name")

        updated = await service.update_role(
            custom_role.id,
            name="Updated Name",
            description="New description",
        )
        assert updated.name == "Updated Name"
        assert updated.description == "New description"

    async def test_delete_role(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Delete a custom role."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="To Delete")
        await service.delete_role(custom_role.id)

        with pytest.raises(TracecatNotFoundError):
            await service.get_role(custom_role.id)

    async def test_delete_role_with_assignments_fails(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Cannot delete role that has active assignments."""
        service = RBACService(session, role=role)

        # Create role and group
        custom_role = await service.create_role(name="Assigned Role")
        group = await service.create_group(name="Test Group")

        # Create assignment
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
        )

        # Try to delete - should fail
        with pytest.raises(TracecatValidationError):
            await service.delete_role(custom_role.id)


@pytest.mark.anyio
class TestRBACServiceGroups:
    """Test group management in RBAC service."""

    async def test_create_group(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create a group."""
        service = RBACService(session, role=role)
        group = await service.create_group(
            name="Engineering Team",
            description="The engineering team",
        )
        assert group.name == "Engineering Team"
        assert group.organization_id == org.id
        assert group.created_by == role.user_id

    async def test_add_member_to_group(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Add a member to a group."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Test Group")

        await service.add_group_member(group.id, user.id)

        members = await service.list_group_members(group.id)
        assert len(members) == 1
        assert members[0][0].id == user.id

    async def test_add_duplicate_member_fails(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Adding same member twice should fail."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Test Group")

        await service.add_group_member(group.id, user.id)

        with pytest.raises(TracecatValidationError):
            await service.add_group_member(group.id, user.id)

    async def test_remove_member_from_group(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Remove a member from a group."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Test Group")

        await service.add_group_member(group.id, user.id)
        await service.remove_group_member(group.id, user.id)

        members = await service.list_group_members(group.id)
        assert len(members) == 0


@pytest.mark.anyio
class TestRBACServiceAssignments:
    """Test group assignment management."""

    async def test_create_org_wide_assignment(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create an org-wide assignment."""
        service = RBACService(session, role=role)

        custom_role = await service.create_role(name="Test Role")
        group = await service.create_group(name="Test Group")

        assignment = await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        assert assignment.organization_id == org.id
        assert assignment.workspace_id is None
        assert assignment.role_id == custom_role.id

    async def test_create_workspace_assignment(
        self,
        session: AsyncSession,
        role: Role,
        workspace: Workspace,
    ):
        """Create a workspace-specific assignment."""
        service = RBACService(session, role=role)

        custom_role = await service.create_role(name="Test Role")
        group = await service.create_group(name="Test Group")

        assignment = await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert assignment.workspace_id == workspace.id

    async def test_update_assignment(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Update an assignment's role."""
        service = RBACService(session, role=role)

        role1 = await service.create_role(name="Role 1")
        role2 = await service.create_role(name="Role 2")
        group = await service.create_group(name="Test Group")

        assignment = await service.create_assignment(
            group_id=group.id,
            role_id=role1.id,
        )

        updated = await service.update_assignment(
            assignment.id,
            role_id=role2.id,
        )

        assert updated.role_id == role2.id


@pytest.mark.anyio
class TestRBACServiceScopeComputation:
    """Test scope computation from group memberships."""

    async def test_get_group_scopes_empty(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """User with no group memberships has no group scopes."""
        service = RBACService(session, role=role)
        scopes = await service.get_group_scopes(user.id)
        assert scopes == frozenset()

    async def test_get_group_scopes_with_assignment(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        seeded_scopes: list[Scope],
    ):
        """User gets scopes from group membership."""
        service = RBACService(session, role=role)

        # Create role with scopes
        scope_ids = [s.id for s in seeded_scopes[:2]]
        custom_role = await service.create_role(
            name="Test Role",
            scope_ids=scope_ids,
        )

        # Create group and add user
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)

        # Create assignment
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
        )

        # Get scopes
        scopes = await service.get_group_scopes(user.id)
        expected_names = {seeded_scopes[0].name, seeded_scopes[1].name}
        assert scopes == expected_names

    async def test_get_group_scopes_workspace_specific(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Workspace-specific assignments only apply when workspace matches."""
        service = RBACService(session, role=role)

        # Create role with scopes
        custom_role = await service.create_role(
            name="Workspace Role",
            scope_ids=[seeded_scopes[0].id],
        )

        # Create group, add user, and assign to specific workspace
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        # Without workspace context, no scopes
        scopes_no_ws = await service.get_group_scopes(user.id, workspace_id=None)
        assert scopes_no_ws == frozenset()

        # With matching workspace, get scopes
        scopes_with_ws = await service.get_group_scopes(
            user.id, workspace_id=workspace.id
        )
        assert seeded_scopes[0].name in scopes_with_ws

    async def test_get_group_scopes_org_wide_applies_to_workspace(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Org-wide assignments apply even when workspace is specified."""
        service = RBACService(session, role=role)

        # Create role with scopes
        custom_role = await service.create_role(
            name="Org Role",
            scope_ids=[seeded_scopes[0].id],
        )

        # Create group, add user, and assign org-wide
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        # With workspace context, org-wide scopes still apply
        scopes = await service.get_group_scopes(user.id, workspace_id=workspace.id)
        assert seeded_scopes[0].name in scopes
