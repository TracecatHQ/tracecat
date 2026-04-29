"""Unit tests for RBAC service."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.rbac.service import RBACService

from tracecat.auth.credentials import _get_membership_with_cache
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


async def _create_external_user(session: AsyncSession, prefix: str) -> User:
    """Create a user without organization membership."""
    user = User(
        id=uuid.uuid4(),
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _has_org_membership(
    session: AsyncSession,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> bool:
    membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == org_id,
        )
    )
    return membership is not None


async def _has_workspace_membership(
    session: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> bool:
    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
        )
    )
    return membership is not None


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

    async def test_create_user_assignment_creates_org_membership_for_non_member(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Creating an org assignment creates org membership when missing."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Direct User Role")

        external_user = await _create_external_user(session, "external-direct")

        await service.create_user_assignment(
            user_id=external_user.id,
            role_id=custom_role.id,
        )

        assert await _has_org_membership(session, external_user.id, org.id)


@pytest.mark.anyio
class TestRBACServiceMembershipSync:
    """Test RBAC assignment mutations synchronize membership rows."""

    async def test_workspace_group_assignment_creates_memberships_for_existing_members(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Workspace group assignment syncs existing group members."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Workspace Group Role")
        group = await service.create_group(name="Workspace Sync Group")
        external_user = await _create_external_user(session, "group-existing")

        await service.add_group_member(group.id, external_user.id)
        assert not await _has_org_membership(session, external_user.id, org.id)

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert await _has_org_membership(session, external_user.id, org.id)
        assert await _has_workspace_membership(session, external_user.id, workspace.id)

    async def test_add_group_member_with_workspace_assignment_syncs_memberships(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Adding a member to a workspace-assigned group creates memberships."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Existing Group Role")
        group = await service.create_group(name="Existing Assignment Group")
        external_user = await _create_external_user(session, "group-add")

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        await service.add_group_member(group.id, external_user.id)

        assert await _has_org_membership(session, external_user.id, org.id)
        assert await _has_workspace_membership(session, external_user.id, workspace.id)

    async def test_remove_group_member_deletes_memberships_without_sources(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Removing a group member removes memberships when no RBAC source remains."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Removal Group Role")
        group = await service.create_group(name="Removal Group")
        external_user = await _create_external_user(session, "group-remove")

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        await service.add_group_member(group.id, external_user.id)
        await service.remove_group_member(group.id, external_user.id)

        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )
        assert not await _has_org_membership(session, external_user.id, org.id)

    async def test_delete_workspace_group_assignment_syncs_all_group_members(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Deleting a workspace group assignment syncs every group member."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Delete Assignment Role")
        group = await service.create_group(name="Delete Assignment Group")
        user_one = await _create_external_user(session, "group-delete-one")
        user_two = await _create_external_user(session, "group-delete-two")

        await service.add_group_member(group.id, user_one.id)
        await service.add_group_member(group.id, user_two.id)
        assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        await service.delete_group_role_assignment(assignment.id)

        for member in (user_one, user_two):
            assert not await _has_workspace_membership(session, member.id, workspace.id)
            assert not await _has_org_membership(session, member.id, org.id)

    async def test_delete_group_syncs_previous_members(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Deleting a group removes memberships that only came from that group."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Delete Group Role")
        group = await service.create_group(name="Delete Group")
        external_user = await _create_external_user(session, "group-delete")

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        await service.add_group_member(group.id, external_user.id)
        await service.delete_group(group.id)

        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )
        assert not await _has_org_membership(session, external_user.id, org.id)

    async def test_org_wide_group_assignment_creates_org_membership_only(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Org-wide group assignment does not create workspace memberships."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Org Group Role")
        group = await service.create_group(name="Org Assignment Group")
        external_user = await _create_external_user(session, "group-org")

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
        )
        await service.add_group_member(group.id, external_user.id)

        assert await _has_org_membership(session, external_user.id, org.id)
        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )

    async def test_direct_org_assignment_syncs_create_update_delete(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Direct org assignment create, update, and delete sync memberships."""
        service = RBACService(session, role=role)
        first_role = await service.create_role(name="Direct Org Role 1")
        second_role = await service.create_role(name="Direct Org Role 2")
        external_user = await _create_external_user(session, "direct-org")

        assignment = await service.create_user_assignment(
            user_id=external_user.id,
            role_id=first_role.id,
        )
        assert await _has_org_membership(session, external_user.id, org.id)
        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )

        updated = await service.update_user_assignment(
            assignment.id,
            role_id=second_role.id,
        )
        assert updated.role_id == second_role.id
        assert await _has_org_membership(session, external_user.id, org.id)
        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )

        await service.delete_user_assignment(assignment.id)
        assert not await _has_org_membership(session, external_user.id, org.id)
        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )

    async def test_direct_workspace_assignment_syncs_create_update_delete(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Direct workspace assignment create, update, and delete sync memberships."""
        service = RBACService(session, role=role)
        first_role = await service.create_role(name="Direct Workspace Role 1")
        second_role = await service.create_role(name="Direct Workspace Role 2")
        external_user = await _create_external_user(session, "direct-workspace")

        assignment = await service.create_user_assignment(
            user_id=external_user.id,
            role_id=first_role.id,
            workspace_id=workspace.id,
        )
        assert await _has_org_membership(session, external_user.id, org.id)
        assert await _has_workspace_membership(session, external_user.id, workspace.id)

        updated = await service.update_user_assignment(
            assignment.id,
            role_id=second_role.id,
        )
        assert updated.role_id == second_role.id
        assert await _has_org_membership(session, external_user.id, org.id)
        assert await _has_workspace_membership(session, external_user.id, workspace.id)

        await service.delete_user_assignment(assignment.id)
        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )
        assert not await _has_org_membership(session, external_user.id, org.id)

    async def test_overlapping_workspace_assignments_keep_membership_until_last_source_removed(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Overlapping direct and group assignments keep membership until both end."""
        service = RBACService(session, role=role)
        direct_role = await service.create_role(name="Overlap Direct Role")
        group_role = await service.create_role(name="Overlap Group Role")
        group = await service.create_group(name="Overlap Group")
        external_user = await _create_external_user(session, "overlap")

        await service.add_group_member(group.id, external_user.id)
        direct_assignment = await service.create_user_assignment(
            user_id=external_user.id,
            role_id=direct_role.id,
            workspace_id=workspace.id,
        )
        group_assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=group_role.id,
            workspace_id=workspace.id,
        )

        await service.delete_user_assignment(direct_assignment.id)
        assert await _has_org_membership(session, external_user.id, org.id)
        assert await _has_workspace_membership(session, external_user.id, workspace.id)

        await service.delete_group_role_assignment(group_assignment.id)
        assert not await _has_workspace_membership(
            session, external_user.id, workspace.id
        )
        assert not await _has_org_membership(session, external_user.id, org.id)

    async def test_workspace_group_assignment_passes_workspace_membership_gate_after_sync(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ):
        """Workspace group assignment creates the row used by the auth gate."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Auth Gate Group Role")
        group = await service.create_group(name="Auth Gate Group")
        external_user = await _create_external_user(session, "auth-gate")

        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )
        await service.add_group_member(group.id, external_user.id)

        request = MagicMock()
        request.state = MagicMock()
        request.state.auth_cache = None

        membership_with_org = await _get_membership_with_cache(
            request=request,
            session=session,
            workspace_id=workspace.id,
            user=external_user,
        )

        assert membership_with_org.org_id == org.id
        assert membership_with_org.membership.user_id == external_user.id
        assert membership_with_org.membership.workspace_id == workspace.id


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
