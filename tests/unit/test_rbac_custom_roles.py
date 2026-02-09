"""High priority RBAC tests for custom roles.

Tests custom role scoping, updates, and union with workspace roles.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.controls import has_scope
from tracecat.authz.enums import OrgRole, ScopeSource, WorkspaceRole
from tracecat.authz.rbac.service import RBACService
from tracecat.authz.scopes import PRESET_ROLE_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    Scope,
    User,
    Workspace,
)

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
# Custom Role Scoping Tests
# =============================================================================


@pytest.mark.anyio
class TestCustomRoleWorkspaceScoping:
    """Test that custom roles are properly scoped to workspaces."""

    async def test_workspace_scoped_grants_exact_scope_in_target_workspace(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Role with {workflow:execute} assigned to ws_a allows in ws_a, denies in ws_b."""
        service = RBACService(session, role=role)

        # Find workflow:execute scope
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if execute_scope is None:
            pytest.skip("workflow:execute scope not found")

        # Create role with workflow:execute and assign to workspace_a only
        custom_role = await service.create_role(
            name="Workflow Executor",
            scope_ids=[execute_scope.id],
        )
        group = await service.create_group(name="Execute Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,  # Scoped to workspace A
        )

        # Check scopes in workspace A - should have execute
        scopes_a = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)
        assert "workflow:execute" in scopes_a

        # Check scopes in workspace B - should NOT have execute
        scopes_b = await service.get_group_scopes(user.id, workspace_id=workspace_b.id)
        assert "workflow:execute" not in scopes_b

    async def test_org_wide_grants_exact_scope_in_all_workspaces(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Same role org-wide allows execute in ws_a and ws_b."""
        service = RBACService(session, role=role)

        # Find workflow:execute scope
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if execute_scope is None:
            pytest.skip("workflow:execute scope not found")

        # Create role with workflow:execute and assign org-wide
        custom_role = await service.create_role(
            name="Org Wide Executor",
            scope_ids=[execute_scope.id],
        )
        group = await service.create_group(name="Org Wide Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        # Check scopes in workspace A - should have execute
        scopes_a = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)
        assert "workflow:execute" in scopes_a

        # Check scopes in workspace B - should also have execute
        scopes_b = await service.get_group_scopes(user.id, workspace_id=workspace_b.id)
        assert "workflow:execute" in scopes_b

    async def test_workspace_scoped_secret_read_bounded(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Role {secret:read} assigned to ws_a allows read in ws_a only."""
        service = RBACService(session, role=role)

        # Find secret:read scope
        secret_scope = next((s for s in seeded_scopes if s.name == "secret:read"), None)
        if secret_scope is None:
            pytest.skip("secret:read scope not found")

        # Create role and assign to workspace_a
        custom_role = await service.create_role(
            name="Secret Reader",
            scope_ids=[secret_scope.id],
        )
        group = await service.create_group(name="Secret Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Check scopes
        scopes_a = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)
        scopes_b = await service.get_group_scopes(user.id, workspace_id=workspace_b.id)

        assert "secret:read" in scopes_a
        assert "secret:read" not in scopes_b


@pytest.mark.anyio
class TestCustomRoleUnionWithWorkspaceRole:
    """Test that custom roles combine with workspace roles correctly."""

    def test_viewer_scopes_are_subset_of_custom_union(self):
        """Verify VIEWER scopes are properly defined for union testing."""
        viewer_scopes = PRESET_ROLE_SCOPES[WorkspaceRole.VIEWER]
        assert "workflow:read" in viewer_scopes
        assert "workflow:execute" not in viewer_scopes
        assert "workflow:update" not in viewer_scopes
        assert "workflow:delete" not in viewer_scopes

    async def test_custom_role_union_allows_execute_but_not_update_delete(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """VIEWER + custom {workflow:execute} can execute but cannot update/delete."""
        service = RBACService(session, role=role)

        # Get VIEWER scopes
        viewer_scopes = PRESET_ROLE_SCOPES[WorkspaceRole.VIEWER]

        # Find workflow:execute scope
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if execute_scope is None:
            pytest.skip("workflow:execute scope not found")

        # Create role with just execute
        custom_role = await service.create_role(
            name="Execute Only",
            scope_ids=[execute_scope.id],
        )
        group = await service.create_group(name="Execute Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Get group scopes
        group_scopes = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )

        # Union of VIEWER + custom role
        combined = viewer_scopes | group_scopes

        # Should have read (from VIEWER) and execute (from custom)
        assert has_scope(combined, "workflow:read")
        assert has_scope(combined, "workflow:execute")

        # Should NOT have update or delete (not in either)
        assert not has_scope(combined, "workflow:update")
        assert not has_scope(combined, "workflow:delete")

    async def test_multi_scope_role_grants_only_listed_scopes(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Role {workflow:read, workflow:execute} allows read+execute, denies create/update/delete."""
        service = RBACService(session, role=role)

        # Find the scopes
        read_scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if read_scope is None or execute_scope is None:
            pytest.skip("Required scopes not found")

        # Create role with read and execute
        custom_role = await service.create_role(
            name="Read and Execute",
            scope_ids=[read_scope.id, execute_scope.id],
        )
        group = await service.create_group(name="Multi Scope Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Get scopes
        scopes = await service.get_group_scopes(user.id, workspace_id=workspace_a.id)

        # Should have exactly what was granted
        assert "workflow:read" in scopes
        assert "workflow:execute" in scopes

        # Should not have what was NOT granted
        assert "workflow:create" not in scopes
        assert "workflow:update" not in scopes
        assert "workflow:delete" not in scopes


@pytest.mark.anyio
class TestCustomRoleUpdates:
    """Test that custom role updates take effect correctly."""

    async def test_role_update_changes_scopes_immediately(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Editing custom role scopes changes allow/deny on next request."""
        service = RBACService(session, role=role)

        # Find scopes
        read_scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if read_scope is None or execute_scope is None:
            pytest.skip("Required scopes not found")

        # Create role with just read scope
        custom_role = await service.create_role(
            name="Evolving Role",
            scope_ids=[read_scope.id],
        )
        group = await service.create_group(name="Evolving Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Initially has read but not execute
        scopes_before = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_before
        assert "workflow:execute" not in scopes_before

        # Update role to also include execute
        await service.update_role(
            custom_role.id,
            scope_ids=[read_scope.id, execute_scope.id],
        )

        # Now should have both
        scopes_after = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_after
        assert "workflow:execute" in scopes_after

    async def test_removing_scope_from_role_revokes_immediately(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Removing a scope from a role removes user's access immediately."""
        service = RBACService(session, role=role)

        # Find scopes
        read_scope = next((s for s in seeded_scopes if s.name == "workflow:read"), None)
        execute_scope = next(
            (s for s in seeded_scopes if s.name == "workflow:execute"), None
        )
        if read_scope is None or execute_scope is None:
            pytest.skip("Required scopes not found")

        # Create role with both scopes
        custom_role = await service.create_role(
            name="Shrinking Role",
            scope_ids=[read_scope.id, execute_scope.id],
        )
        group = await service.create_group(name="Shrinking Group")
        await service.add_group_member(group.id, user.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_a.id,
        )

        # Initially has both
        scopes_before = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_before
        assert "workflow:execute" in scopes_before

        # Update role to only have read (remove execute)
        await service.update_role(
            custom_role.id,
            scope_ids=[read_scope.id],  # Execute removed
        )

        # Now should only have read
        scopes_after = await service.get_group_scopes(
            user.id, workspace_id=workspace_a.id
        )
        assert "workflow:read" in scopes_after
        assert "workflow:execute" not in scopes_after
