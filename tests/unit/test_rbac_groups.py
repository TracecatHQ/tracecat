"""High priority RBAC tests for groups.

Tests group scope inheritance, multiple group unions, and revocation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole, ScopeSource
from tracecat.authz.rbac.service import RBACService
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    Scope,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

# =============================================================================
# Fixtures
# =============================================================================


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
        email=f"user-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace_a(session: AsyncSession, org: Organization) -> Workspace:
    """Create workspace A."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Workspace A",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def workspace_b(session: AsyncSession, org: Organization) -> Workspace:
    """Create workspace B."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Workspace B",
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
    """Create a test Role object."""
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        service_id="tracecat-api",
    )


# =============================================================================
# Group Scope Inheritance Tests
# =============================================================================


@pytest.mark.anyio
class TestGroupWorkspaceScoping:
    """Test that group assignments are properly scoped to workspaces."""

    async def test_workspace_scoped_assignment_grants_only_in_target_workspace(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Group assignment to ws_a grants only in ws_a, not ws_b."""
        service = RBACService(session, role=role)

        # Find a scope
        scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        if scope is None:
            pytest.skip("workflow:read scope not found")

        # Create role and group, assign to workspace A only
        custom_role = await service.create_role(
            name="Workspace A Role",
            scope_ids=[scope.id],
        )
        group = await service.create_group(name="WS A Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Check scopes
        scopes_a = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)
        scopes_b = await service.get_group_scopes(user.id, workspace_id=workspace_b.id)

        assert "workflow:read" in scopes_a
        assert "workflow:read" not in scopes_b

    async def test_org_wide_assignment_grants_in_all_workspaces(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Org-wide group assignment grants in ws_a and ws_b."""
        service = RBACService(session, role=role)

        # Find a scope
        scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        if scope is None:
            pytest.skip("workflow:read scope not found")

        # Create role and group, assign org-wide
        custom_role = await service.create_role(
            name="Org Wide Role",
            scope_ids=[scope.id],
        )
        group = await service.create_group(name="Org Wide Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        # Check scopes
        scopes_a = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)
        scopes_b = await service.get_group_scopes(user.id, workspace_id=workspace_b.id)

        assert "workflow:read" in scopes_a
        assert "workflow:read" in scopes_b


@pytest.mark.anyio
class TestMultipleGroupUnion:
    """Test that multiple group memberships combine correctly."""

    async def test_multiple_groups_union_scopes(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Group A grants workflow:read, Group B grants workflow:execute → user gets both."""
        service = RBACService(session, role=role)

        # Find scopes
        read_scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if read_scope is None or execute_scope is None:
            pytest.skip("Required scopes not found")

        # Create Group A with read scope
        role_a = await service.create_role(
            name="Reader Role", scope_ids=[read_scope.id]
        )
        group_a = await service.create_group(name="Readers")
        await service.add_group_member(group_a.id, user.id)
        await service.create_assignment(
            group_id=group_a.id,
            role_id=role_a.id,
            workspace_id=workspace_a.id,
        )

        # Create Group B with execute scope
        role_b = await service.create_role(
            name="Executor Role", scope_ids=[execute_scope.id]
        )
        group_b = await service.create_group(name="Executors")
        await service.add_group_member(group_b.id, user.id)
        await service.create_assignment(
            group_id=group_b.id,
            role_id=role_b.id,
            workspace_id=workspace_a.id,
        )

        # User should have both scopes
        scopes = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)
        assert "workflow:read" in scopes
        assert "workflow:execute" in scopes

    async def test_group_and_direct_assignment_union(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Direct assignment + group assignment combine as union."""
        service = RBACService(session, role=role)

        # Find scopes
        read_scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if read_scope is None or execute_scope is None:
            pytest.skip("Required scopes not found")

        # Create group with read scope
        group_role = await service.create_role(
            name="Group Role", scope_ids=[read_scope.id]
        )
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=group_role.id,
            workspace_id=workspace_a.id,
        )

        # Create direct user assignment with execute scope
        user_role = await service.create_role(
            name="User Role", scope_ids=[execute_scope.id]
        )
        await service.create_user_assignment(
            user_id=user.id,
            role_id=user_role.id,
            workspace_id=workspace_a.id,
        )

        # Get scopes from both sources
        group_scopes = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        user_scopes = await service.get_user_role_scopes(
            user.id, workspace_id=workspace_a.id
        )

        # Combined should have both
        combined = group_scopes | user_scopes
        assert "workflow:read" in combined
        assert "workflow:execute" in combined


@pytest.mark.anyio
class TestGroupMembershipRevocation:
    """Test that removing group membership revokes scopes immediately."""

    async def test_group_membership_removal_revokes_scopes(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Removing user from group immediately denies previously allowed scope."""
        service = RBACService(session, role=role)

        # Find a scope
        scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        if scope is None:
            pytest.skip("workflow:read scope not found")

        # Create role and group
        custom_role = await service.create_role(
            name="Revoke Test Role",
            scope_ids=[scope.id],
        )
        group = await service.create_group(name="Revoke Test Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Initially has scope
        scopes_before = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_before

        # Remove from group
        await service.remove_group_member(group.id, user.id)

        # Should no longer have scope
        scopes_after = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" not in scopes_after

    async def test_group_role_assignment_removal_revokes_scopes(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Removing assignment immediately denies previously allowed scope."""
        service = RBACService(session, role=role)

        # Find a scope
        scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        if scope is None:
            pytest.skip("workflow:read scope not found")

        # Create role and group
        custom_role = await service.create_role(
            name="Assignment Revoke Role",
            scope_ids=[scope.id],
        )
        group = await service.create_group(name="Assignment Revoke Group")
        await service.add_group_member(group.id, user.id)
        assignment = await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Initially has scope
        scopes_before = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_before

        # Remove assignment
        await service.delete_assignment(assignment.id)

        # Should no longer have scope
        scopes_after = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" not in scopes_after


@pytest.mark.anyio
class TestUserRoleAssignmentRevocation:
    """Test that user role assignment revocation works correctly."""

    async def test_user_assignment_removal_revokes_scopes(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Removing user role assignment immediately revokes scopes."""
        service = RBACService(session, role=role)

        # Find a scope
        scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        if scope is None:
            pytest.skip("workflow:read scope not found")

        # Create role and direct assignment
        custom_role = await service.create_role(
            name="Direct Assignment Role",
            scope_ids=[scope.id],
        )
        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Initially has scope
        scopes_before = await service.get_user_role_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_before

        # Remove assignment
        await service.delete_user_assignment(assignment.id)

        # Should no longer have scope
        scopes_after = await service.get_user_role_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" not in scopes_after


@pytest.mark.anyio
class TestGroupBoundaryValidation:
    """Test boundary validation for group operations."""

    async def test_cannot_add_non_existent_user_to_group(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Cannot add a user that doesn't exist to a group."""
        service = RBACService(session, role=role)

        group = await service.create_group(name="Boundary Test Group")
        fake_user_id = uuid.uuid4()

        with pytest.raises(TracecatNotFoundError):
            await service.add_group_member(group.id, fake_user_id)

    async def test_cannot_add_duplicate_member(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Cannot add the same user twice to a group."""
        service = RBACService(session, role=role)

        group = await service.create_group(name="Duplicate Test Group")
        await service.add_group_member(group.id, user.id)

        # Try to add again
        with pytest.raises(TracecatValidationError):
            await service.add_group_member(group.id, user.id)

    async def test_cannot_remove_non_member_from_group(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Cannot remove a user who isn't a member."""
        service = RBACService(session, role=role)

        group = await service.create_group(name="Remove Test Group")
        # User is NOT added to group

        with pytest.raises(TracecatNotFoundError):
            await service.remove_group_member(group.id, user.id)


@pytest.mark.anyio
class TestGroupEffectiveScopes:
    """Test the get_user_effective_scopes breakdown."""

    async def test_effective_scopes_shows_breakdown(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """get_user_effective_scopes returns breakdown of scope sources."""
        service = RBACService(session, role=role)

        # Find scopes
        read_scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if read_scope is None or execute_scope is None:
            pytest.skip("Required scopes not found")

        # Create group with read scope
        group_role = await service.create_role(
            name="Group Breakdown Role",
            scope_ids=[read_scope.id],
        )
        group = await service.create_group(name="Breakdown Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=group_role.id,
            workspace_id=workspace_a.id,
        )

        # Create direct user assignment with execute scope
        user_role = await service.create_role(
            name="User Breakdown Role",
            scope_ids=[execute_scope.id],
        )
        await service.create_user_assignment(
            user_id=user.id,
            role_id=user_role.id,
            workspace_id=workspace_a.id,
        )

        # Get effective scopes breakdown
        breakdown = await service.get_user_effective_scopes(
            user.id, workspace_id=workspace_a.id
        )

        # Verify breakdown structure
        assert "group_scopes" in breakdown
        assert "user_role_scopes" in breakdown

        # Verify correct attribution
        assert "workflow:read" in breakdown["group_scopes"]
        assert "workflow:execute" in breakdown["user_role_scopes"]
