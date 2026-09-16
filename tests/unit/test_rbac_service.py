"""Unit tests for RBAC service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.rbac.schemas import (
    RoleAssignmentSnapshot,
    UserRoleAssignmentSpec,
    UserRoleAssignmentsReplace,
)
from tracecat_ee.rbac.service import RBACService

from tests.support.membership import (
    grant_org_membership,
    grant_org_membership_via_group,
    grant_workspace_membership,
)
from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.authz.membership import ensure_member
from tracecat.authz.scopes import ORG_ADMIN_SCOPES, ORG_MEMBER_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.authz.service import MembershipService, query_effective_scopes
from tracecat.db.models import (
    AccessToken,
    Group,
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    MCPRefreshToken,
    Organization,
    OrganizationMembership,
    RoleScope,
    Scope,
    ServiceAccount,
    ServiceAccountApiKey,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.organization.router import get_organization
from tracecat.workspaces.service import WorkspaceService


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

    # Presence through a group keeps the direct org-wide slot free for tests.
    await grant_org_membership_via_group(
        session, user_id=user.id, organization_id=org.id
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
    await ensure_member(session, org.id, admin_user.id)
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
        await grant_org_membership_via_group(
            session, user_id=user.id, organization_id=other_org.id
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

    async def test_create_user_assignment_for_workspace_only_user(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """A workspace-only path is enough to grant an org-wide role."""
        member = await _workspace_only_user(session, org, workspace)
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Org Wide Role")

        assignment = await service.create_user_assignment(
            user_id=member.id,
            role_id=custom_role.id,
        )

        assert assignment.workspace_id is None
        assert assignment.user_id == member.id

    async def test_add_group_member_for_workspace_only_user(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """A workspace-only path is enough to add the user to a group."""
        member = await _workspace_only_user(session, org, workspace)
        service = RBACService(session, role=role)
        group = await service.create_group(name="Workspace Only Group")

        await service.add_group_member(group.id, member.id)

        result = await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.user_id == member.id,
            )
        )
        assert result.scalar_one_or_none() is not None

    async def test_delete_org_wide_assignment_with_workspace_path(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """A remaining workspace path keeps the org-wide role deletable."""
        member = await _workspace_only_user(session, org, workspace)
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Removable Org Role")
        assignment = await service.create_user_assignment(
            user_id=member.id,
            role_id=custom_role.id,
        )

        await service.delete_user_assignment(assignment.id)

        remaining = (
            await session.execute(
                select(UserRoleAssignment).where(UserRoleAssignment.id == assignment.id)
            )
        ).scalar_one_or_none()
        assert remaining is None

    async def test_delete_org_wide_assignment_with_group_path(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """A remaining group path keeps the org-wide role deletable."""
        member = User(
            id=uuid.uuid4(),
            email=f"grouppath-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="test",
        )
        session.add(member)
        await session.flush()
        await grant_org_membership_via_group(
            session, user_id=member.id, organization_id=org.id
        )
        await session.commit()

        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Group Path Org Role")
        assignment = await service.create_user_assignment(
            user_id=member.id,
            role_id=custom_role.id,
        )

        await service.delete_user_assignment(assignment.id)

        remaining = (
            await session.execute(
                select(UserRoleAssignment).where(UserRoleAssignment.id == assignment.id)
            )
        ).scalar_one_or_none()
        assert remaining is None

    async def test_create_org_assignment_writes_legacy_org_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Org-wide assignment mirrors presence into the legacy org table."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Legacy Org Role")

        await service.create_user_assignment(user_id=user.id, role_id=custom_role.id)

        assert (
            await _org_membership_row(session, user.id, service.organization_id)
            is not None
        )

    async def test_create_org_assignment_tolerates_existing_membership_row(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """A pre-existing membership row does not break the upsert."""
        service = RBACService(session, role=role)
        await ensure_member(session, service.organization_id, user.id)
        await session.commit()

        custom_role = await service.create_role(name="Legacy Org Role Again")

        await service.create_user_assignment(user_id=user.id, role_id=custom_role.id)

        assert (
            await _org_membership_row(session, user.id, service.organization_id)
            is not None
        )

    async def test_create_workspace_assignment_writes_legacy_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
    ):
        """Workspace-scoped assignment mirrors presence into the legacy table."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Legacy Workspace Role")

        await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert await _legacy_workspace_row(session, user.id, workspace.id) is not None

    async def test_delete_last_org_assignment_keeps_org_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Org presence is stored, so it outlives the last org-wide assignment."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Evictable Org Role")
        assignment = await service.create_user_assignment(
            user_id=user.id, role_id=custom_role.id
        )

        await service.delete_user_assignment(assignment.id)

        assert (
            await _org_membership_row(session, user.id, service.organization_id)
            is not None
        )

    async def test_delete_org_assignment_keeps_membership_in_both_orgs(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        org: Organization,
    ):
        """Deleting an assignment never removes presence in any organization."""
        other_org_id = uuid.uuid4()
        other_org = Organization(
            id=other_org_id,
            name="Other Org",
            slug=f"other-org-{other_org_id.hex[:8]}",
        )
        session.add(other_org)
        await session.flush()
        await grant_org_membership_via_group(
            session, user_id=user.id, organization_id=other_org.id
        )
        await session.commit()

        service = RBACService(session, role=role)
        other_service = RBACService(
            session, role=role.model_copy(update={"organization_id": other_org.id})
        )
        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=(await service.create_role(name="Leaving Org Role")).id,
        )
        await other_service.create_user_assignment(
            user_id=user.id,
            role_id=(await other_service.create_role(name="Kept Org Role")).id,
        )

        await service.delete_user_assignment(assignment.id)

        assert await _org_membership_row(session, user.id, org.id) is not None
        assert await _org_membership_row(session, user.id, other_org.id) is not None

    async def test_delete_last_workspace_assignment_removes_legacy_membership(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        org: Organization,
        workspace: Workspace,
    ):
        """Legacy eviction is scoped to the workspace being left."""
        other_workspace = Workspace(
            id=uuid.uuid4(),
            name="Other Workspace",
            organization_id=org.id,
        )
        session.add(other_workspace)
        await session.commit()

        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Evictable Workspace Role")
        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=other_workspace.id,
        )

        await service.delete_user_assignment(assignment.id)

        assert await _legacy_workspace_row(session, user.id, workspace.id) is None
        assert (
            await _legacy_workspace_row(session, user.id, other_workspace.id)
            is not None
        )


async def _org_membership_row(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> OrganizationMembership | None:
    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


async def _legacy_workspace_row(
    session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> LegacyMembership | None:
    result = await session.execute(
        select(LegacyMembership).where(
            LegacyMembership.user_id == user_id,
            LegacyMembership.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def _workspace_only_user(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> User:
    """Create a user whose only role path is workspace-scoped."""
    member = User(
        id=uuid.uuid4(),
        email=f"wsonly-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    session.add(member)
    await session.flush()
    await grant_workspace_membership(
        session,
        user_id=member.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await session.commit()
    return member


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


async def _org_assignment(
    session: AsyncSession, user_id: uuid.UUID
) -> UserRoleAssignment:
    return (
        await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
    ).scalar_one()


async def _workspace_group(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
) -> tuple[Group, GroupRoleAssignment]:
    editor_id = (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "workspace-editor",
            )
        )
    ).scalar_one()
    group = Group(
        name=f"Workspace access {uuid.uuid4().hex[:8]}", organization_id=org.id
    )
    session.add(group)
    await session.flush()
    grant = GroupRoleAssignment(
        group_id=group.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        role_id=editor_id,
    )
    session.add_all([grant, GroupMember(group_id=group.id, user_id=user.id)])
    await session.commit()
    return group, grant


@pytest.mark.anyio
class TestBaselineTransitions:
    @pytest.mark.parametrize("via_group", [False, True])
    async def test_promotion_and_removal_preserve_workspace_use(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        via_group: bool,
    ):
        member = await _workspace_only_user(session, org, workspace)
        if via_group:
            await _workspace_group(session, org, workspace, member)
            await session.execute(
                delete(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == member.id,
                    UserRoleAssignment.workspace_id == workspace.id,
                )
            )
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        baseline = await _org_assignment(session, member.id)
        admin_id = (
            await session.execute(
                select(DBRole.id).where(
                    DBRole.organization_id == org.id,
                    DBRole.slug == "organization-admin",
                )
            )
        ).scalar_one()
        service = RBACService(session, role=role)
        promoted = await service.update_user_assignment(baseline.id, role_id=admin_id)
        assert promoted.id == baseline.id
        assert promoted.role_id == admin_id

        await service.delete_user_assignment(promoted.id)
        restored = await _org_assignment(session, member.id)
        restored_role = await session.get(DBRole, restored.role_id)
        assert restored_role is not None
        assert restored_role.slug == "organization-member"
        scopes = await query_effective_scopes(session, member.id, org.id, None)
        assert scopes == ORG_MEMBER_SCOPES
        reader = Role(
            type="user",
            user_id=member.id,
            organization_id=org.id,
            service_id="tracecat-api",
            scopes=scopes,
        )
        assert (await get_organization(role=reader, session=session)).id == org.id
        workspace_scopes = await query_effective_scopes(
            session, member.id, org.id, workspace.id
        )
        assert {"workflow:create", "case:create"}.issubset(workspace_scopes)
        assert "org:rbac:delete" not in workspace_scopes
        # Direct API deletion cannot strip the fallback while workspace access remains.
        await service.delete_user_assignment(restored.id)
        assert (await _org_assignment(session, member.id)).id == restored.id
        assert await query_effective_scopes(session, member.id, org.id, None) == scopes

    async def test_admin_alone_leaves_org_and_sessions_are_revoked(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(
            session,
            user_id=member.id,
            organization_id=org.id,
            slug="organization-admin",
        )
        await session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.user_id == member.id,
                UserRoleAssignment.workspace_id == workspace.id,
            )
        )
        token = AccessToken(token=uuid.uuid4().hex, user_id=member.id)
        refresh = MCPRefreshToken(
            organization_id=org.id,
            user_id=member.id,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            family_id=uuid.uuid4(),
            client_id="test-client",
            encrypted_metadata=b"{}",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add_all([token, refresh])
        await session.commit()
        grant = await _org_assignment(session, member.id)
        await RBACService(session, role=role).delete_user_assignment(grant.id)
        assert not (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == member.id,
                    OrganizationMembership.organization_id == org.id,
                )
            )
        ).all()
        assert not (
            await session.execute(select(AccessToken).where(AccessToken.id == token.id))
        ).all()
        await session.refresh(refresh)
        assert refresh.status == "revoked"
        assert await session.get(User, member.id) is not None

    @pytest.mark.parametrize("through_workspace_api", [False, True])
    async def test_last_workspace_removal_does_not_leave_baseline_membership(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        through_workspace_api: bool,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        if through_workspace_api:
            await MembershipService(session, role=role).delete_membership(
                workspace.id, member.id
            )
        else:
            grant_id = (
                await session.execute(
                    select(UserRoleAssignment.id).where(
                        UserRoleAssignment.user_id == member.id,
                        UserRoleAssignment.workspace_id == workspace.id,
                    )
                )
            ).scalar_one()
            await RBACService(session, role=role).delete_user_assignment(grant_id)
        assert not (
            await session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == member.id,
                )
            )
        ).all()

    @pytest.mark.parametrize("operation", ["membership", "assignment", "group"])
    async def test_final_group_path_removal_cleans_up_baseline(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        operation: str,
    ):
        member = await _workspace_only_user(session, org, workspace)
        group, grant = await _workspace_group(session, org, workspace, member)
        await session.execute(
            delete(UserRoleAssignment).where(UserRoleAssignment.user_id == member.id)
        )
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        service = RBACService(session, role=role)
        if operation == "membership":
            await service.remove_group_member(group.id, member.id)
        elif operation == "assignment":
            await service.delete_group_role_assignment(grant.id)
        else:
            await service.delete_group(group.id)
        assert not (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == member.id,
                )
            )
        ).all()

    async def test_losing_group_org_role_restores_baseline_for_direct_workspace(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        group = await grant_org_membership_via_group(
            session, user_id=member.id, organization_id=org.id
        )
        await session.commit()
        await RBACService(session, role=role).remove_group_member(group.id, member.id)
        assert (
            await query_effective_scopes(session, member.id, org.id, None)
            == ORG_MEMBER_SCOPES
        )

    async def test_last_role_removal_preserves_superuser_guard_and_rolls_back(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        member.is_superuser = True
        await session.commit()
        grant_id = (
            await session.execute(
                select(UserRoleAssignment.id).where(
                    UserRoleAssignment.user_id == member.id,
                    UserRoleAssignment.workspace_id == workspace.id,
                )
            )
        ).scalar_one()
        with pytest.raises(TracecatAuthorizationError, match="Cannot delete superuser"):
            await RBACService(session, role=role).delete_user_assignment(grant_id)
        assert await session.get(UserRoleAssignment, grant_id) is not None

    async def test_baseline_cannot_be_assigned_as_an_ordinary_role(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        baseline_id = (
            await session.execute(
                select(DBRole.id).where(
                    DBRole.organization_id == org.id,
                    DBRole.slug == "organization-member",
                )
            )
        ).scalar_one()
        with pytest.raises(TracecatAuthorizationError, match="managed automatically"):
            await RBACService(session, role=role).create_user_assignment(
                user_id=member.id,
                role_id=baseline_id,
            )


async def _replacement(
    service: RBACService, user_id: uuid.UUID
) -> UserRoleAssignmentsReplace:
    direct = await service.list_user_assignments(user_id=user_id)
    groups = await service.list_group_role_assignments(user_id=user_id)
    return UserRoleAssignmentsReplace(
        user_id=user_id,
        assignments=[
            UserRoleAssignmentSpec.model_validate(a, from_attributes=True)
            for a in direct
        ],
        expected_assignments=[
            RoleAssignmentSnapshot.model_validate(a, from_attributes=True)
            for a in direct
        ],
        expected_group_assignments=[
            RoleAssignmentSnapshot.model_validate(a, from_attributes=True)
            for a in groups
        ],
    )


@pytest.mark.anyio
class TestAtomicRoleEdits:
    async def test_move_final_workspace_and_promote_in_one_save(
        self, session: AsyncSession, role: Role, org: Organization, workspace: Workspace
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        other = Workspace(name="Destination", organization_id=org.id)
        token = AccessToken(token=uuid.uuid4().hex, user_id=member.id)
        session.add_all([other, token])
        await session.commit()
        service = RBACService(session, role=role)
        params = await _replacement(service, member.id)
        original_org = next(
            a for a in params.expected_assignments if a.workspace_id is None
        )
        editor = next(a.role_id for a in params.assignments if a.workspace_id)
        admin = await session.scalar(
            select(DBRole.id).where(
                DBRole.organization_id == org.id, DBRole.slug == "organization-admin"
            )
        )
        assert admin
        params.assignments = [
            UserRoleAssignmentSpec(role_id=admin),
            UserRoleAssignmentSpec(role_id=editor, workspace_id=other.id),
        ]
        await service.replace_user_assignments(params)
        assert (await _org_assignment(session, member.id)).id == original_org.id
        assert (
            await session.scalar(
                select(AccessToken.id).where(AccessToken.id == token.id)
            )
            is not None
        )
        assert "workflow:create" in await query_effective_scopes(
            session, member.id, org.id, other.id
        )
        # Removing the promoted organization role restores its hidden fallback.
        params = await _replacement(service, member.id)
        params.assignments = [a for a in params.assignments if a.workspace_id]
        await service.replace_user_assignments(params)
        assert (
            await query_effective_scopes(session, member.id, org.id, None)
            == ORG_MEMBER_SCOPES
        )

        assert "workflow:create" not in await query_effective_scopes(
            session, member.id, org.id, workspace.id
        )

    @pytest.mark.parametrize(
        "failure",
        [
            "invalid_role",
            "cross_org_workspace",
            "duplicate_scope",
            "grant_ceiling",
            "missing_permission",
        ],
    )
    async def test_invalid_edit_saves_nothing(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        privileged_role: DBRole,
        failure: str,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        service = RBACService(session, role=role)
        params = await _replacement(service, member.id)
        before = params.expected_assignments
        admin = await session.scalar(
            select(DBRole.id).where(
                DBRole.organization_id == org.id, DBRole.slug == "organization-admin"
            )
        )
        assert admin
        params.assignments = [UserRoleAssignmentSpec(role_id=admin)]
        error: type[Exception] = TracecatNotFoundError
        if failure == "invalid_role":
            params.assignments.append(
                UserRoleAssignmentSpec(role_id=uuid.uuid4(), workspace_id=workspace.id)
            )
        elif failure == "cross_org_workspace":
            other_org = Organization(name="Other", slug=uuid.uuid4().hex)
            session.add(other_org)
            await session.flush()
            foreign = Workspace(name="Foreign", organization_id=other_org.id)
            session.add(foreign)
            await session.commit()
            params.assignments.append(
                UserRoleAssignmentSpec(role_id=admin, workspace_id=foreign.id)
            )
        elif failure == "duplicate_scope":
            params.assignments.append(UserRoleAssignmentSpec(role_id=admin))
            error = TracecatValidationError
        elif failure == "grant_ceiling":
            params.assignments.append(
                UserRoleAssignmentSpec(
                    role_id=privileged_role.id, workspace_id=workspace.id
                )
            )
            error = TracecatAuthorizationError
        else:
            service = RBACService(
                session,
                role=role.model_copy(
                    update={"scopes": frozenset({"org:rbac:read", "org:rbac:update"})}
                ),
            )
            error = ScopeDeniedError
        with pytest.raises(error):
            await service.replace_user_assignments(params)
        assert (
            await _replacement(service, params.user_id)
        ).expected_assignments == before

    @pytest.mark.parametrize("group_change", [False, True])
    async def test_stale_access_rejected_before_writes(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        group_change: bool,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        service = RBACService(session, role=role)
        params = await _replacement(service, member.id)
        params.assignments = []
        if group_change:
            await _workspace_group(session, org, workspace, member)
        else:
            assignment = await _org_assignment(session, member.id)
            assignment.role_id = next(
                a.role_id for a in params.expected_assignments if a.workspace_id
            )
            await session.commit()
        before = (await _replacement(service, member.id)).expected_assignments
        with pytest.raises(TracecatConflictError):
            await service.replace_user_assignments(params)
        assert (
            await _replacement(service, params.user_id)
        ).expected_assignments == before

    async def test_cleanup_failure_rolls_back_whole_edit(
        self, session: AsyncSession, role: Role, org: Organization, workspace: Workspace
    ):
        member = await _workspace_only_user(session, org, workspace)
        member.is_superuser = True
        await session.commit()
        service = RBACService(session, role=role)
        params = await _replacement(service, member.id)
        params.assignments = []
        with pytest.raises(TracecatAuthorizationError, match="Cannot delete superuser"):
            await service.replace_user_assignments(params)
        assert (
            await _replacement(service, params.user_id)
        ).expected_assignments == params.expected_assignments

    async def test_group_filter_excludes_unrelated_members_and_organizations(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        user: User,
    ):
        member = await _workspace_only_user(session, org, workspace)
        _, own_grant = await _workspace_group(session, org, workspace, member)
        await _workspace_group(session, org, workspace, user)
        other_org = Organization(name="Other", slug=uuid.uuid4().hex)
        session.add(other_org)
        await session.flush()
        await grant_org_membership_via_group(
            session, user_id=member.id, organization_id=other_org.id
        )
        await session.commit()
        grants = await RBACService(session, role=role).list_group_role_assignments(
            user_id=member.id
        )
        assert [a.id for a in grants] == [own_grant.id]


@pytest.mark.anyio
class TestWorkspaceDeletionReconciliation:
    @pytest.mark.parametrize("via_group", [False, True])
    @pytest.mark.parametrize(
        "remaining", ["none", "direct_workspace", "group_workspace", "org_admin"]
    )
    async def test_delete_reconciles_all_affected_users(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
        via_group: bool,
        remaining: str,
    ):
        member = await _workspace_only_user(session, org, workspace)
        if via_group:
            await _workspace_group(session, org, workspace, member)
            await session.execute(
                delete(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == member.id
                )
            )
        await grant_org_membership(
            session,
            user_id=member.id,
            organization_id=org.id,
            slug="organization-admin"
            if remaining == "org_admin"
            else "organization-member",
        )
        other = Workspace(name="Surviving workspace", organization_id=org.id)
        session.add(other)
        await session.flush()
        if remaining == "direct_workspace":
            await grant_workspace_membership(
                session,
                user_id=member.id,
                organization_id=org.id,
                workspace_id=other.id,
            )
        elif remaining == "group_workspace":
            await _workspace_group(session, org, other, member)
        token = AccessToken(token=uuid.uuid4().hex, user_id=member.id)
        session.add(token)
        await session.commit()
        member_id, token_id = member.id, token.id
        await WorkspaceService(session, role=role).delete_workspace(workspace.id)
        present = await session.scalar(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.user_id == member_id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        assert bool(present) == (remaining != "none")
        assert bool(
            await session.scalar(
                select(AccessToken.id).where(AccessToken.id == token_id)
            )
        ) == (remaining != "none")
        scopes = await query_effective_scopes(session, member_id, org.id, None)
        assert ("org:read" in scopes) == (remaining != "none")
        if "workspace" in remaining:
            assert "workflow:create" in await query_effective_scopes(
                session, member_id, org.id, other.id
            )

    async def test_cleanup_failure_preserves_workspace_and_grants(
        self, session: AsyncSession, role: Role, org: Organization, workspace: Workspace
    ):
        member = await _workspace_only_user(session, org, workspace)
        member.is_superuser = True
        session.add(Workspace(name="Other", organization_id=org.id))
        await session.commit()
        workspace_id, member_id, org_id = workspace.id, member.id, org.id
        with pytest.raises(TracecatAuthorizationError, match="Cannot delete superuser"):
            await WorkspaceService(session, role=role).delete_workspace(workspace_id)
        assert (
            await session.scalar(
                select(Workspace.id).where(Workspace.id == workspace_id)
            )
            is not None
        )
        assert "workflow:create" in await query_effective_scopes(
            session, member_id, org_id, workspace_id
        )

    async def test_final_access_revokes_owned_keys_without_deleting_records(
        self, session: AsyncSession, role: Role, org: Organization, workspace: Workspace
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        other_org = Organization(name="Other", slug=uuid.uuid4().hex)
        session.add(other_org)
        await session.flush()
        accounts = [
            ServiceAccount(
                name="Owned", organization_id=org.id, owner_user_id=member.id
            ),
            ServiceAccount(
                name="Another owner", organization_id=org.id, owner_user_id=role.user_id
            ),
            ServiceAccount(
                name="Other organization",
                organization_id=other_org.id,
                owner_user_id=member.id,
            ),
        ]
        session.add_all(accounts)
        session.add(Workspace(name="Other", organization_id=org.id))
        await session.flush()
        keys = [
            ServiceAccountApiKey(
                service_account_id=a.id,
                name="Test key",
                key_id=uuid.uuid4().hex,
                hashed="synthetic",
                salt="synthetic",
                preview="synthetic",
                created_by=member.id,
            )
            for a in accounts
        ]
        session.add_all(keys)
        await session.commit()
        await WorkspaceService(session, role=role).delete_workspace(workspace.id)
        for index, (account, key) in enumerate(zip(accounts, keys, strict=True)):
            await session.refresh(account)
            await session.refresh(key)
            assert (account.disabled_at is not None) == (index == 0)
            assert (key.revoked_at is not None) == (index == 0)
            if index == 0:
                assert key.revoked_by == role.user_id


@pytest.fixture
def rbac_only_role(role: Role) -> Role:
    """An actor with full org RBAC authority but no member-removal scope."""
    return role.model_copy(
        update={"scopes": ORG_ADMIN_SCOPES - frozenset({"org:member:remove"})}
    )


@pytest.mark.anyio
class TestEvictionRequiresRemovalScope:
    """Reconcile-driven eviction is member removal, so RBAC scopes alone deny it."""

    async def test_replace_to_empty_is_denied_and_rolled_back(
        self,
        session: AsyncSession,
        rbac_only_role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        service = RBACService(session, role=rbac_only_role)
        params = await _replacement(service, member.id)
        before = params.expected_assignments
        params.assignments = []
        with pytest.raises(TracecatAuthorizationError):
            await service.replace_user_assignments(params)
        assert (
            await _replacement(service, params.user_id)
        ).expected_assignments == before

    async def test_delete_final_assignment_is_denied_and_rolled_back(
        self,
        session: AsyncSession,
        rbac_only_role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        grant_id = (
            await session.execute(
                select(UserRoleAssignment.id).where(
                    UserRoleAssignment.user_id == member.id,
                    UserRoleAssignment.workspace_id == workspace.id,
                )
            )
        ).scalar_one()
        with pytest.raises(TracecatAuthorizationError):
            await RBACService(session, role=rbac_only_role).delete_user_assignment(
                grant_id
            )
        assert await session.get(UserRoleAssignment, grant_id) is not None

    async def test_remove_final_group_member_is_denied_and_rolled_back(
        self,
        session: AsyncSession,
        rbac_only_role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        group, _ = await _workspace_group(session, org, workspace, member)
        await session.execute(
            delete(UserRoleAssignment).where(UserRoleAssignment.user_id == member.id)
        )
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        group_id, member_id = group.id, member.id
        with pytest.raises(TracecatAuthorizationError):
            await RBACService(session, role=rbac_only_role).remove_group_member(
                group_id, member_id
            )
        assert (
            await session.scalar(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == member_id,
                )
            )
        ) is not None

    async def test_removing_a_non_final_role_still_succeeds(
        self,
        session: AsyncSession,
        rbac_only_role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(
            session,
            user_id=member.id,
            organization_id=org.id,
            slug="organization-admin",
        )
        await session.commit()
        grant_id = (
            await session.execute(
                select(UserRoleAssignment.id).where(
                    UserRoleAssignment.user_id == member.id,
                    UserRoleAssignment.workspace_id == workspace.id,
                )
            )
        ).scalar_one()
        await RBACService(session, role=rbac_only_role).delete_user_assignment(grant_id)
        assert await session.get(UserRoleAssignment, grant_id) is None
        assert await _org_assignment(session, member.id) is not None

    async def test_removal_scope_allows_eviction(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        member = await _workspace_only_user(session, org, workspace)
        await grant_org_membership(session, user_id=member.id, organization_id=org.id)
        await session.commit()
        service = RBACService(session, role=role)
        params = await _replacement(service, member.id)
        params.assignments = []
        await service.replace_user_assignments(params)
        assert not (
            await session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == member.id
                )
            )
        ).all()
