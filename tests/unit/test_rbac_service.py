"""Unit tests for RBAC service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.rbac.service import RBACService

from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    Group,
    GroupMember,
    Membership,
    Organization,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
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
        select(Scope).where(Scope.source == ScopeSource.PLATFORM)
    )
    return list(result.scalars().all())


@pytest.fixture
def role(org: Organization, user: User) -> Role:
    """Create a test role for the service."""
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=ORG_ADMIN_SCOPES,
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
            include_system=True, source=ScopeSource.PLATFORM
        )
        assert all(s.source == ScopeSource.PLATFORM for s in scopes)

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
        await service.create_group_role_assignment(
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

    async def test_remove_member_rejects_cross_org_group(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Removing a member should not affect groups outside the caller org."""
        service = RBACService(session, role=role)

        other_org_id = uuid.uuid4()
        other_org = Organization(
            id=other_org_id,
            name="Other Org",
            slug=f"other-org-{other_org_id.hex[:8]}",
        )
        other_user = User(
            id=uuid.uuid4(),
            email="other-rbac-user@example.com",
            hashed_password="test",
        )
        other_group = Group(
            name="Other Org Group",
            organization_id=other_org.id,
            created_by=other_user.id,
        )
        session.add_all([other_org, other_user])
        await session.flush()
        session.add(other_group)
        await session.flush()
        session.add(
            GroupMember(
                group_id=other_group.id,
                user_id=other_user.id,
            )
        )
        await session.commit()

        with pytest.raises(TracecatNotFoundError):
            await service.remove_group_member(other_group.id, other_user.id)

        remaining_member = await session.scalar(
            select(GroupMember).where(
                GroupMember.group_id == other_group.id,
                GroupMember.user_id == other_user.id,
            )
        )
        assert remaining_member is not None

    async def test_list_group_members_excludes_cross_org_group(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Listing members should not return rows for groups in another org."""
        service = RBACService(session, role=role)

        other_org_id = uuid.uuid4()
        other_org = Organization(
            id=other_org_id,
            name="Other List Org",
            slug=f"other-list-org-{other_org_id.hex[:8]}",
        )
        other_user = User(
            id=uuid.uuid4(),
            email="other-list-rbac-user@example.com",
            hashed_password="test",
        )
        other_group = Group(
            name="Other List Group",
            organization_id=other_org.id,
            created_by=other_user.id,
        )
        session.add_all([other_org, other_user])
        await session.flush()
        session.add(other_group)
        await session.flush()
        session.add(
            GroupMember(
                group_id=other_group.id,
                user_id=other_user.id,
            )
        )
        await session.commit()

        members = await service.list_group_members(other_group.id)
        assert members == []


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

        assignment = await service.create_group_role_assignment(
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

        assignment = await service.create_group_role_assignment(
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

        assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=role1.id,
        )

        updated = await service.update_group_role_assignment(
            assignment.id,
            role_id=role2.id,
        )

        assert updated.role_id == role2.id


@pytest.mark.anyio
class TestRBACServiceUserAssignments:
    """Test direct user role assignment management."""

    async def test_create_user_assignment_for_org_member(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Create direct assignment for org member."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Direct User Role")

        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
        )

        assert assignment.user_id == user.id
        assert assignment.role_id == custom_role.id
        assert assignment.organization_id == role.organization_id

    async def test_create_org_wide_user_assignments_across_organizations(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """A user can have one org-wide direct role assignment per org."""
        other_org_id = uuid.uuid4()
        other_org = Organization(
            id=other_org_id,
            name="Other Org",
            slug=f"other-org-{other_org_id.hex[:8]}",
        )
        session.add(other_org)
        await session.flush()
        session.add(
            OrganizationMembership(
                user_id=user.id,
                organization_id=other_org.id,
            )
        )
        await session.commit()

        service = RBACService(session, role=role)
        other_role = role.model_copy(update={"organization_id": other_org.id})
        other_service = RBACService(session, role=other_role)

        org_role = await service.create_role(name="Direct User Role")
        other_org_role = await other_service.create_role(name="Direct User Role")

        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=org_role.id,
        )
        other_assignment = await other_service.create_user_assignment(
            user_id=user.id,
            role_id=other_org_role.id,
        )

        assert assignment.organization_id == role.organization_id
        assert other_assignment.organization_id == other_org.id

        result = await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
        org_ids = {assignment.organization_id for assignment in result.scalars()}
        assert org_ids == {role.organization_id, other_org.id}

    async def test_create_duplicate_org_wide_user_assignment_in_same_org_fails(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """A user can still have only one direct org-wide assignment per org."""
        service = RBACService(session, role=role)
        first_role = await service.create_role(name="First Direct User Role")
        second_role = await service.create_role(name="Second Direct User Role")

        await service.create_user_assignment(
            user_id=user.id,
            role_id=first_role.id,
        )

        with pytest.raises(
            TracecatValidationError,
            match="User already has an assignment for this workspace",
        ):
            await service.create_user_assignment(
                user_id=user.id,
                role_id=second_role.id,
            )

    async def test_create_user_assignment_rejects_non_member(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Cannot assign org role to user outside organization."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Direct User Role")

        external_user = User(
            id=uuid.uuid4(),
            email="external@example.com",
            hashed_password="test",
        )
        session.add(external_user)
        await session.commit()

        with pytest.raises(
            TracecatNotFoundError, match="User not found in organization"
        ):
            await service.create_user_assignment(
                user_id=external_user.id,
                role_id=custom_role.id,
            )


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
        await service.create_group_role_assignment(
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
        await service.create_group_role_assignment(
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
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        # With workspace context, org-wide scopes still apply
        scopes = await service.get_group_scopes(user.id, workspace_id=workspace.id)
        assert seeded_scopes[0].name in scopes


@pytest.mark.anyio
class TestRBACMembershipReconcile:
    """RBAC mutations keep the workspace membership dial in lockstep (ENG-1499)."""

    async def _membership(
        self, session: AsyncSession, workspace: Workspace, user: User
    ) -> Membership | None:
        return await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace.id,
                Membership.user_id == user.id,
            )
        )

    async def test_ws_scoped_group_assignment_materializes_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Creating a ws-scoped group assignment mints membership for members."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="WS Role", scope_ids=[seeded_scopes[0].id]
        )
        group = await service.create_group(name="WS Group")
        await service.add_group_member(group.id, user.id)

        assert await self._membership(session, workspace, user) is None

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert await self._membership(session, workspace, user) is not None

    async def test_org_wide_group_assignment_does_not_materialize_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Org-wide group assignment grants scopes but not workspace membership."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="Org Role", scope_ids=[seeded_scopes[0].id]
        )
        group = await service.create_group(name="Org Group")
        await service.add_group_member(group.id, user.id)
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,
        )

        assert await self._membership(session, workspace, user) is None

    async def test_adding_member_to_assigned_group_materializes_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Adding a user to a group that already holds a ws assignment mints membership."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="WS Role", scope_ids=[seeded_scopes[0].id]
        )
        group = await service.create_group(name="WS Group")
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert await self._membership(session, workspace, user) is None

        await service.add_group_member(group.id, user.id)

        assert await self._membership(session, workspace, user) is not None

    async def test_direct_user_ws_assignment_materializes_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """A direct ws-scoped user assignment mints membership."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="WS Role", scope_ids=[seeded_scopes[0].id]
        )

        await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert await self._membership(session, workspace, user) is not None

    async def test_deleting_only_ws_path_removes_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Deleting the sole ws-scoped path drops membership."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="WS Role", scope_ids=[seeded_scopes[0].id]
        )
        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        assert await self._membership(session, workspace, user) is not None

        await service.delete_user_assignment(assignment.id)

        assert await self._membership(session, workspace, user) is None

    async def test_deleting_group_removes_materialized_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Deleting a ws-assigned group drops membership for its members.

        The cascade removes GroupMember/GroupRoleAssignment but not the derived
        Membership rows, so delete_group must reconcile the affected members.
        """
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="WS Role", scope_ids=[seeded_scopes[0].id]
        )
        group = await service.create_group(name="WS Group")
        await service.add_group_member(group.id, user.id)
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        assert await self._membership(session, workspace, user) is not None

        await service.delete_group(group.id)

        assert await self._membership(session, workspace, user) is None

    async def test_deleting_group_keeps_membership_with_other_path(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """A direct path keeps membership alive when the group is deleted."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="WS Role", scope_ids=[seeded_scopes[0].id]
        )
        group = await service.create_group(name="WS Group")
        await service.add_group_member(group.id, user.id)
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        # Independent direct path to the same workspace.
        await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        assert await self._membership(session, workspace, user) is not None

        await service.delete_group(group.id)

        assert await self._membership(session, workspace, user) is not None


async def _role_with_scopes(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    name: str,
    scope_names: set[str],
) -> DBRole:
    """Create a DB role wired to the given scope names."""
    db_role = DBRole(id=uuid.uuid4(), name=name, slug=None, organization_id=org_id)
    session.add(db_role)
    await session.flush()
    for scope_name in scope_names:
        # Reuse an existing scope (unique on org_id + name) or create one.
        existing = await session.scalar(
            select(Scope).where(
                Scope.organization_id == org_id, Scope.name == scope_name
            )
        )
        if existing is None:
            resource, _, action = scope_name.rpartition(":")
            existing = Scope(
                id=uuid.uuid4(),
                name=scope_name,
                resource=resource or scope_name,
                action=action or "execute",
                source=ScopeSource.CUSTOM,
                organization_id=org_id,
            )
            session.add(existing)
            await session.flush()
        session.add(RoleScope(role_id=db_role.id, scope_id=existing.id))
    await session.commit()
    return db_role


@pytest.mark.anyio
class TestListRolesScopeVisibility:
    """list_roles must hide roles that grant more access than the requester."""

    async def test_superset_roles_are_hidden(
        self, session: AsyncSession, org: Organization, user: User
    ) -> None:
        from tracecat.authz.rbac.router import list_roles

        await _role_with_scopes(
            session, org.id, name="Subset", scope_names={"workspace:read"}
        )
        await _role_with_scopes(
            session,
            org.id,
            name="Superset",
            scope_names={"workspace:read", "workspace:member:remove"},
        )
        requester = Role(
            type="user",
            user_id=user.id,
            organization_id=org.id,
            service_id="tracecat-api",
            is_platform_superuser=False,
            scopes=frozenset({"workspace:read"}),
        )
        result = await list_roles(role=requester, session=session)
        names = {r.name for r in result.items}
        assert "Subset" in names
        assert "Superset" not in names

    async def test_superuser_sees_all_roles(
        self, session: AsyncSession, org: Organization, user: User
    ) -> None:
        from tracecat.authz.rbac.router import list_roles

        await _role_with_scopes(
            session,
            org.id,
            name="Elevated",
            scope_names={"workspace:member:remove"},
        )
        superuser = Role(
            type="user",
            user_id=user.id,
            organization_id=org.id,
            service_id="tracecat-api",
            is_platform_superuser=True,
            scopes=frozenset({"*"}),
        )
        result = await list_roles(role=superuser, session=session)
        assert "Elevated" in {r.name for r in result.items}
