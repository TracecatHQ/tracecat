"""Unit tests for RBAC service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.rbac.service import RBACService

from tests.membership import grant_workspace_membership
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    Organization,
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

    # Grant membership through a workspace-scoped role so the single org-wide
    # assignment slot stays free for tests that create one.
    membership_workspace = Workspace(
        id=uuid.uuid4(),
        name="Membership Workspace",
        organization_id=org.id,
    )
    session.add(membership_workspace)
    await session.flush()
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=membership_workspace.id,
    )
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
def admin_assignable_scopes(seeded_scopes: list[Scope]) -> list[Scope]:
    """Return seeded scopes held by the organization-admin test role."""
    return [scope for scope in seeded_scopes if scope.name in ORG_ADMIN_SCOPES]


@pytest.fixture
async def privileged_role(
    session: AsyncSession,
    org: Organization,
    user: User,
    seeded_scopes: list[Scope],
) -> DBRole:
    """Create a pre-existing role with an owner-only scope."""
    owner_scope = next(
        scope for scope in seeded_scopes if scope.name == "org:owner:assign"
    )
    privileged_role = DBRole(
        name="Privileged Test Role",
        slug=None,
        description=None,
        organization_id=org.id,
        created_by=user.id,
    )
    session.add(privileged_role)
    await session.flush()
    session.add(RoleScope(role_id=privileged_role.id, scope_id=owner_scope.id))
    await session.commit()
    await session.refresh(privileged_role, ["scopes"])
    return privileged_role


@pytest.fixture
async def role(
    session: AsyncSession,
    org: Organization,
    seeded_scopes: list[Scope],
) -> Role:
    """Create a test role for the service.

    The caller is a separate admin user backed by a real org-wide assignment:
    grant ceilings read the caller's scopes from the database, so an
    in-memory-only role would be denied. Keeping the caller distinct from the
    ``user`` fixture leaves that user's assignment slots free for tests.
    """
    admin_user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password="test",
    )
    session.add(admin_user)
    await session.flush()

    # The org-wide assignment below is what makes this user an org member.
    admin_role = DBRole(
        name="Test Org Admin",
        slug=None,
        description=None,
        organization_id=org.id,
        created_by=admin_user.id,
    )
    session.add(admin_role)
    await session.flush()
    for scope in seeded_scopes:
        if scope.name in ORG_ADMIN_SCOPES:
            session.add(RoleScope(role_id=admin_role.id, scope_id=scope.id))
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=admin_user.id,
            workspace_id=None,
            role_id=admin_role.id,
        )
    )
    await session.commit()

    return Role(
        type="user",
        user_id=admin_user.id,
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
        admin_assignable_scopes: list[Scope],
    ):
        """Create a role with scopes assigned."""
        service = RBACService(session, role=role)
        scope_ids = [scope.id for scope in admin_assignable_scopes[:3]]

        custom_role = await service.create_role(
            name="Custom Role With Scopes",
            scope_ids=scope_ids,
        )
        assert len(custom_role.scopes) == 3

    async def test_create_role_rejects_unheld_scopes(
        self,
        session: AsyncSession,
        role: Role,
        privileged_role: DBRole,
    ):
        """Creating a role cannot grant scopes outside the caller's ceiling."""
        service = RBACService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
            await service.create_role(
                name="Escalated Role",
                scope_ids=[privileged_role.scopes[0].id],
            )

    async def test_update_role_rejects_unheld_scopes(
        self,
        session: AsyncSession,
        role: Role,
        privileged_role: DBRole,
    ):
        """Updating a role cannot grant scopes outside the caller's ceiling."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Assignable Role")

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
            await service.update_role(
                custom_role.id,
                scope_ids=[privileged_role.scopes[0].id],
            )

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
        assert updated.updated_at >= updated.created_at

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

    async def test_update_group(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Updated groups expose server-generated timestamps without lazy IO."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Original Group")

        updated = await service.update_group(
            group.id,
            name="Updated Group",
            description="New description",
        )

        assert updated.name == "Updated Group"
        assert updated.description == "New description"
        assert updated.updated_at >= updated.created_at

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

    async def test_add_member_rejects_unheld_group_role(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        user: User,
        privileged_role: DBRole,
    ):
        """Adding a member cannot grant scopes outside the caller's ceiling."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Privileged Group")
        session.add(
            GroupRoleAssignment(
                organization_id=org.id,
                group_id=group.id,
                role_id=privileged_role.id,
                workspace_id=None,
                assigned_by=user.id,
            )
        )
        await session.commit()

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
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

    async def test_create_assignment_rejects_unheld_role(
        self,
        session: AsyncSession,
        role: Role,
        privileged_role: DBRole,
    ):
        """Group assignment creation enforces the caller's scope ceiling."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Target Group")

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
            await service.create_group_role_assignment(
                group_id=group.id,
                role_id=privileged_role.id,
            )

    async def test_update_assignment_rejects_unheld_role(
        self,
        session: AsyncSession,
        role: Role,
        privileged_role: DBRole,
    ):
        """Group assignment updates enforce the caller's scope ceiling."""
        service = RBACService(session, role=role)
        assignable_role = await service.create_role(name="Assignable Role")
        group = await service.create_group(name="Target Group")
        assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=assignable_role.id,
        )

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
            await service.update_group_role_assignment(
                assignment.id,
                role_id=privileged_role.id,
            )


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
        # Workspace-scoped grant keeps the org-wide slot free for the assertion.
        other_workspace = Workspace(
            id=uuid.uuid4(),
            name="Other Org Workspace",
            organization_id=other_org.id,
        )
        session.add(other_workspace)
        await session.flush()
        await grant_workspace_membership(
            session,
            user_id=user.id,
            organization_id=other_org.id,
            workspace_id=other_workspace.id,
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

    async def test_create_user_assignment_rejects_unheld_role(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        privileged_role: DBRole,
    ):
        """User assignment creation enforces the caller's scope ceiling."""
        service = RBACService(session, role=role)

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
            await service.create_user_assignment(
                user_id=user.id,
                role_id=privileged_role.id,
            )

    async def test_update_user_assignment_rejects_unheld_role(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        privileged_role: DBRole,
    ):
        """User assignment updates enforce the caller's scope ceiling."""
        service = RBACService(session, role=role)
        assignable_role = await service.create_role(name="Assignable Role")
        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=assignable_role.id,
        )

        with pytest.raises(
            TracecatAuthorizationError,
            match="Cannot grant scopes not held by the caller",
        ):
            await service.update_user_assignment(
                assignment.id,
                role_id=privileged_role.id,
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
        admin_assignable_scopes: list[Scope],
    ):
        """User gets scopes from group membership."""
        service = RBACService(session, role=role)

        # Create role with scopes
        scope_ids = [scope.id for scope in admin_assignable_scopes[:2]]
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
        expected_names = {
            admin_assignable_scopes[0].name,
            admin_assignable_scopes[1].name,
        }
        assert scopes == expected_names

    async def test_get_group_scopes_workspace_specific(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        admin_assignable_scopes: list[Scope],
    ):
        """Workspace-specific assignments only apply when workspace matches."""
        service = RBACService(session, role=role)

        # Create role with scopes
        custom_role = await service.create_role(
            name="Workspace Role",
            scope_ids=[admin_assignable_scopes[0].id],
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
        assert admin_assignable_scopes[0].name in scopes_with_ws

    async def test_get_group_scopes_org_wide_applies_to_workspace(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        admin_assignable_scopes: list[Scope],
    ):
        """Org-wide assignments apply even when workspace is specified."""
        service = RBACService(session, role=role)

        # Create role with scopes
        custom_role = await service.create_role(
            name="Org Role",
            scope_ids=[admin_assignable_scopes[0].id],
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
        assert admin_assignable_scopes[0].name in scopes
