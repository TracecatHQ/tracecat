"""Critical RBAC tests for tenant isolation.

Tests IDOR/cross-tenant access prevention - the most critical security boundary.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.controls import has_scope
from tracecat.authz.enums import OrgRole, ScopeSource
from tracecat.authz.rbac.service import RBACService
from tracecat.authz.seeding import seed_system_scopes
from tracecat.contexts import ctx_scopes
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    Scope,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def org_a(session: AsyncSession) -> Organization:
    """Create organization A for tenant isolation tests."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Org A", slug=f"org-a-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def org_b(session: AsyncSession) -> Organization:
    """Create organization B for tenant isolation tests."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Org B", slug=f"org-b-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def user_in_org_a(session: AsyncSession, org_a: Organization) -> User:
    """Create a user who is a member of org_a only."""
    user = User(
        id=uuid.uuid4(),
        email=f"user-a-{uuid.uuid4().hex[:6]}@org-a.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org_a.id,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def user_in_org_b(session: AsyncSession, org_b: Organization) -> User:
    """Create a user who is a member of org_b only."""
    user = User(
        id=uuid.uuid4(),
        email=f"user-b-{uuid.uuid4().hex[:6]}@org-b.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org_b.id,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace_in_org_a(session: AsyncSession, org_a: Organization) -> Workspace:
    """Create a workspace in org_a."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Workspace A",
        organization_id=org_a.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def workspace_in_org_b(session: AsyncSession, org_b: Organization) -> Workspace:
    """Create a workspace in org_b."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Workspace B",
        organization_id=org_b.id,
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
def role_for_org_a(org_a: Organization, user_in_org_a: User) -> Role:
    """Create a Role object for user in org_a."""
    return Role(
        type="user",
        user_id=user_in_org_a.id,
        organization_id=org_a.id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        service_id="tracecat-api",
    )


@pytest.fixture
def role_for_org_b(org_b: Organization, user_in_org_b: User) -> Role:
    """Create a Role object for user in org_b."""
    return Role(
        type="user",
        user_id=user_in_org_b.id,
        organization_id=org_b.id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        service_id="tracecat-api",
    )


# =============================================================================
# Critical Tenant Isolation Tests
# =============================================================================


@pytest.mark.anyio
class TestCrossOrgRBACIsolation:
    """Test that RBAC entities are isolated between organizations."""

    async def test_cannot_list_roles_from_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """User in org_a cannot see roles created in org_b."""
        # Create a role in org_b
        service_b = RBACService(session, role=role_for_org_b)
        role_b = await service_b.create_role(name="Org B Only Role")

        # User in org_a lists roles - should not see org_b's role
        service_a = RBACService(session, role=role_for_org_a)
        roles_a = await service_a.list_roles()

        role_ids_a = {r.id for r in roles_a}
        assert role_b.id not in role_ids_a

    async def test_cannot_get_role_from_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """User in org_a cannot access a specific role from org_b."""
        # Create a role in org_b
        service_b = RBACService(session, role=role_for_org_b)
        role_b = await service_b.create_role(name="Secret Org B Role")

        # User in org_a tries to get the role - should get not found
        service_a = RBACService(session, role=role_for_org_a)
        with pytest.raises(TracecatNotFoundError):
            await service_a.get_role(role_b.id)

    async def test_cannot_list_groups_from_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """User in org_a cannot see groups created in org_b."""
        # Create a group in org_b
        service_b = RBACService(session, role=role_for_org_b)
        group_b = await service_b.create_group(name="Org B Security Team")

        # User in org_a lists groups - should not see org_b's group
        service_a = RBACService(session, role=role_for_org_a)
        groups_a = await service_a.list_groups()

        group_ids_a = {g.id for g in groups_a}
        assert group_b.id not in group_ids_a

    async def test_cannot_get_group_from_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """User in org_a cannot access a specific group from org_b."""
        # Create a group in org_b
        service_b = RBACService(session, role=role_for_org_b)
        group_b = await service_b.create_group(name="Secret Org B Group")

        # User in org_a tries to get the group - should get not found
        service_a = RBACService(session, role=role_for_org_a)
        with pytest.raises(TracecatNotFoundError):
            await service_a.get_group(group_b.id)

    async def test_cannot_list_assignments_from_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """User in org_a cannot see group assignments from org_b."""
        # Create group and role in org_b, then create assignment
        service_b = RBACService(session, role=role_for_org_b)
        role_b = await service_b.create_role(name="Org B Role")
        group_b = await service_b.create_group(name="Org B Group")
        assignment_b = await service_b.create_assignment(
            group_id=group_b.id,
            role_id=role_b.id,
        )

        # User in org_a lists assignments - should not see org_b's assignment
        service_a = RBACService(session, role=role_for_org_a)
        assignments_a = await service_a.list_assignments()

        assignment_ids_a = {a.id for a in assignments_a}
        assert assignment_b.id not in assignment_ids_a


@pytest.mark.anyio
class TestCrossWorkspaceIsolation:
    """Test that workspace-scoped permissions don't leak across workspaces."""

    async def test_workspace_scoped_assignment_does_not_apply_to_other_workspace(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        user_in_org_a: User,
        workspace_in_org_a: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Scopes from workspace A assignment should not apply to workspace B."""
        service = RBACService(session, role=role_for_org_a)

        # Create another workspace in org_a
        workspace_b = Workspace(
            id=uuid.uuid4(),
            name="Workspace B in Org A",
            organization_id=role_for_org_a.organization_id,
        )
        session.add(workspace_b)
        await session.commit()

        # Create role with specific scope and assign to workspace_a
        custom_role = await service.create_role(
            name="Workspace A Only",
            scope_ids=[seeded_scopes[0].id],
        )
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user_in_org_a.id)
        await service.create_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace_in_org_a.id,  # Scoped to workspace A
        )

        # Get scopes for workspace A - should have the scope
        scopes_ws_a = await service.get_group_scopes(
            user_in_org_a.id, workspace_id=workspace_in_org_a.id
        )
        assert seeded_scopes[0].name in scopes_ws_a

        # Get scopes for workspace B - should NOT have the scope
        scopes_ws_b = await service.get_group_scopes(
            user_in_org_a.id, workspace_id=workspace_b.id
        )
        assert seeded_scopes[0].name not in scopes_ws_b

    async def test_cannot_assign_role_to_workspace_in_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        workspace_in_org_b: Workspace,
    ):
        """Cannot create an assignment scoped to a workspace in another org."""
        service = RBACService(session, role=role_for_org_a)

        # Create role and group in org_a
        custom_role = await service.create_role(name="Cross-Org Test Role")
        group = await service.create_group(name="Cross-Org Test Group")

        # Try to assign to workspace in org_b - should fail
        with pytest.raises(TracecatNotFoundError):
            await service.create_assignment(
                group_id=group.id,
                role_id=custom_role.id,
                workspace_id=workspace_in_org_b.id,  # Wrong org!
            )


@pytest.mark.anyio
class TestScopeContextIsolation:
    """Test that scope context is properly isolated per request."""

    def test_scope_context_is_request_scoped(self):
        """Verify that ctx_scopes is isolated between contexts."""
        # Set scopes for "request 1"
        scopes_1 = frozenset({"workflow:read", "case:read"})
        token_1 = ctx_scopes.set(scopes_1)

        # Verify scopes are set
        assert ctx_scopes.get() == scopes_1

        # Reset and set different scopes for "request 2"
        ctx_scopes.reset(token_1)
        scopes_2 = frozenset({"workflow:execute"})
        token_2 = ctx_scopes.set(scopes_2)

        # Verify new scopes
        assert ctx_scopes.get() == scopes_2
        assert ctx_scopes.get() != scopes_1

        ctx_scopes.reset(token_2)

    def test_scope_check_uses_current_context_only(self):
        """Scope checks must use current context, not cached values."""
        # User has workflow:read in ws_a context
        scopes_ws_a = frozenset({"workflow:read"})
        token = ctx_scopes.set(scopes_ws_a)

        assert has_scope(ctx_scopes.get(), "workflow:read") is True
        assert has_scope(ctx_scopes.get(), "workflow:execute") is False

        ctx_scopes.reset(token)

        # Same user in ws_b context has different scopes
        scopes_ws_b = frozenset({"workflow:execute"})
        token = ctx_scopes.set(scopes_ws_b)

        assert has_scope(ctx_scopes.get(), "workflow:execute") is True
        assert has_scope(ctx_scopes.get(), "workflow:read") is False

        ctx_scopes.reset(token)


@pytest.mark.anyio
class TestCustomScopeIsolation:
    """Test that custom scopes are isolated between organizations."""

    async def test_custom_scope_only_visible_to_creating_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """Custom scopes created in org_a should not be visible to org_b."""
        # Create custom scope in org_a
        service_a = RBACService(session, role=role_for_org_a)
        await service_a.create_scope(
            name="custom:org-a-only",
            description="A custom scope for org A",
        )

        # Org_a can see it
        scopes_a = await service_a.list_scopes(include_system=False)
        scope_names_a = {s.name for s in scopes_a}
        assert "custom:org-a-only" in scope_names_a

        # Org_b cannot see it (only sees system scopes and their own)
        service_b = RBACService(session, role=role_for_org_b)
        scopes_b = await service_b.list_scopes(include_system=False)
        scope_names_b = {s.name for s in scopes_b}
        assert "custom:org-a-only" not in scope_names_b

    async def test_cannot_get_custom_scope_from_other_org(
        self,
        session: AsyncSession,
        role_for_org_a: Role,
        role_for_org_b: Role,
    ):
        """User in org_b cannot access a custom scope from org_a by ID."""
        # Create custom scope in org_a
        service_a = RBACService(session, role=role_for_org_a)
        custom_scope = await service_a.create_scope(name="custom:secret")

        # Org_b tries to get it by ID - should fail
        service_b = RBACService(session, role=role_for_org_b)
        with pytest.raises(TracecatNotFoundError):
            await service_b.get_scope(custom_scope.id)
