"""Tests for RBAC scope and role seeding."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from tracecat.authz.enums import ScopeSource, WorkspaceRole
from tracecat.authz.scopes import SYSTEM_ROLE_SCOPES
from tracecat.authz.seeding import (
    SYSTEM_ROLE_DEFINITIONS,
    SYSTEM_SCOPE_DEFINITIONS,
    get_system_scope_ids,
    seed_registry_scope,
    seed_registry_scopes_bulk,
    seed_system_roles_for_org,
    seed_system_scopes,
)
from tracecat.db.models import Organization, Role, RoleScope, Scope


@pytest.mark.anyio
async def test_seed_system_scopes(session):
    """Test that system scopes are seeded correctly."""
    # Seed system scopes
    inserted_count = await seed_system_scopes(session)

    # Should have inserted all scopes on first run
    assert inserted_count == len(SYSTEM_SCOPE_DEFINITIONS)

    # Verify scopes exist in database
    result = await session.execute(
        select(Scope).where(
            Scope.source == ScopeSource.SYSTEM,
            Scope.organization_id.is_(None),
        )
    )
    scopes = result.scalars().all()
    assert len(scopes) == len(SYSTEM_SCOPE_DEFINITIONS)

    # Verify each scope has correct attributes
    scope_names = {s.name for s in scopes}
    expected_names = {name for name, _, _, _ in SYSTEM_SCOPE_DEFINITIONS}
    assert scope_names == expected_names


@pytest.mark.anyio
async def test_seed_system_scopes_idempotent(session):
    """Test that seeding is idempotent - running twice doesn't duplicate."""
    # Seed once
    first_count = await seed_system_scopes(session)
    assert first_count == len(SYSTEM_SCOPE_DEFINITIONS)

    # Seed again
    second_count = await seed_system_scopes(session)
    assert second_count == 0  # No new scopes inserted

    # Verify count is still the same
    result = await session.execute(
        select(Scope).where(
            Scope.source == ScopeSource.SYSTEM,
            Scope.organization_id.is_(None),
        )
    )
    scopes = result.scalars().all()
    assert len(scopes) == len(SYSTEM_SCOPE_DEFINITIONS)


@pytest.mark.anyio
async def test_seed_registry_scope(session):
    """Test seeding a single registry scope."""
    action_key = "tools.test_integration.test_action"

    scope = await seed_registry_scope(session, action_key, "Test action scope")
    await session.commit()

    assert scope is not None
    assert scope.name == f"action:{action_key}:execute"
    assert scope.resource == "action"
    assert scope.action == "execute"
    assert scope.source == ScopeSource.REGISTRY
    assert scope.source_ref == action_key
    assert scope.organization_id is None


@pytest.mark.anyio
async def test_seed_registry_scope_idempotent(session):
    """Test that seeding the same registry scope twice returns existing scope."""
    action_key = "tools.test_integration.test_action_idempotent"

    # Seed first time
    scope1 = await seed_registry_scope(session, action_key)
    await session.commit()

    # Seed second time
    scope2 = await seed_registry_scope(session, action_key)

    assert scope1 is not None
    assert scope2 is not None
    assert scope1.id == scope2.id


@pytest.mark.anyio
async def test_seed_registry_scopes_bulk(session):
    """Test bulk seeding of registry scopes."""
    action_keys = [
        "tools.okta.list_users",
        "tools.okta.create_user",
        "tools.zendesk.create_ticket",
        "core.http_request",
    ]

    inserted_count = await seed_registry_scopes_bulk(session, action_keys)
    await session.commit()

    assert inserted_count == len(action_keys)

    # Verify scopes exist
    result = await session.execute(
        select(Scope).where(
            Scope.source == ScopeSource.REGISTRY,
            Scope.organization_id.is_(None),
        )
    )
    scopes = result.scalars().all()
    assert len(scopes) >= len(action_keys)

    scope_names = {s.name for s in scopes}
    for key in action_keys:
        assert f"action:{key}:execute" in scope_names


@pytest.mark.anyio
async def test_seed_registry_scopes_bulk_idempotent(session):
    """Test that bulk seeding is idempotent."""
    action_keys = ["tools.test.action1", "tools.test.action2"]

    # First seed
    first_count = await seed_registry_scopes_bulk(session, action_keys)
    await session.commit()
    assert first_count == 2

    # Second seed
    second_count = await seed_registry_scopes_bulk(session, action_keys)
    await session.commit()
    assert second_count == 0


@pytest.mark.anyio
async def test_seed_registry_scopes_bulk_empty(session):
    """Test bulk seeding with empty list."""
    inserted_count = await seed_registry_scopes_bulk(session, [])
    assert inserted_count == 0


@pytest.mark.anyio
async def test_get_system_scope_ids(session):
    """Test retrieving system scope IDs by name."""
    # First seed the system scopes
    await seed_system_scopes(session)

    # Get scope IDs for a subset of scopes
    scope_names = frozenset({"workflow:read", "workflow:create", "case:read"})
    scope_ids = await get_system_scope_ids(session, scope_names)

    assert len(scope_ids) == 3
    assert "workflow:read" in scope_ids
    assert "workflow:create" in scope_ids
    assert "case:read" in scope_ids


@pytest.mark.anyio
async def test_get_system_scope_ids_with_wildcards(session):
    """Test retrieving scope IDs including wildcard scopes."""
    # First seed the system scopes
    await seed_system_scopes(session)

    # ADMIN_SCOPES includes wildcard scopes like "action:*:execute"
    scope_names = frozenset({"workflow:read", "action:*:execute"})
    scope_ids = await get_system_scope_ids(session, scope_names)

    # Should find both exact and wildcard scopes
    assert "workflow:read" in scope_ids
    assert "action:*:execute" in scope_ids


@pytest.mark.anyio
async def test_seed_system_roles_for_org(session):
    """Test seeding system roles for an organization."""
    # Create a test organization first
    org = Organization(
        id=uuid4(),
        name="Test Org for Roles",
        slug=f"test-org-roles-{uuid4().hex[:8]}",
    )
    session.add(org)
    await session.flush()

    # Seed system scopes first (roles depend on these)
    await seed_system_scopes(session)

    # Seed system roles for the organization
    created_count = await seed_system_roles_for_org(session, org.id)

    assert created_count == len(SYSTEM_ROLE_DEFINITIONS)

    # Verify roles exist
    result = await session.execute(
        select(Role).where(
            Role.organization_id == org.id,
            Role.is_system.is_(True),
        )
    )
    roles = result.scalars().all()
    assert len(roles) == len(SYSTEM_ROLE_DEFINITIONS)

    role_names = {r.name for r in roles}
    expected_names = {name for name, _ in SYSTEM_ROLE_DEFINITIONS.values()}
    assert role_names == expected_names


@pytest.mark.anyio
async def test_seed_system_roles_for_org_idempotent(session):
    """Test that role seeding is idempotent."""
    # Create a test organization
    org = Organization(
        id=uuid4(),
        name="Test Org for Idempotent Roles",
        slug=f"test-org-idem-{uuid4().hex[:8]}",
    )
    session.add(org)
    await session.flush()

    # Seed system scopes first
    await seed_system_scopes(session)

    # Seed roles twice
    first_count = await seed_system_roles_for_org(session, org.id)
    second_count = await seed_system_roles_for_org(session, org.id)

    assert first_count == len(SYSTEM_ROLE_DEFINITIONS)
    assert second_count == 0  # No new roles on second run


@pytest.mark.anyio
async def test_seed_system_roles_have_scopes(session):
    """Test that seeded system roles have the correct scopes assigned."""
    # Create a test organization
    org = Organization(
        id=uuid4(),
        name="Test Org for Role Scopes",
        slug=f"test-org-scopes-{uuid4().hex[:8]}",
    )
    session.add(org)
    await session.flush()

    # Seed system scopes first
    await seed_system_scopes(session)

    # Seed system roles
    await seed_system_roles_for_org(session, org.id)

    # Verify Viewer role has correct scopes
    result = await session.execute(
        select(Role).where(
            Role.organization_id == org.id,
            Role.name == "Viewer",
        )
    )
    viewer_role = result.scalar_one()

    # Get the role-scope assignments
    result = await session.execute(
        select(RoleScope).where(RoleScope.role_id == viewer_role.id)
    )
    role_scopes = result.scalars().all()

    # Viewer role should have scopes assigned
    # The exact count depends on which scopes exist in the database
    # (wildcards may not all have individual scope entries)
    viewer_scope_names = SYSTEM_ROLE_SCOPES[WorkspaceRole.VIEWER]
    assert len(role_scopes) > 0

    # Verify at least some expected scopes are present
    result = await session.execute(
        select(Scope.name).where(Scope.id.in_([rs.scope_id for rs in role_scopes]))
    )
    assigned_scope_names = set(result.scalars().all())

    # Check that non-wildcard scopes are assigned
    for scope_name in viewer_scope_names:
        if "*" not in scope_name:
            assert scope_name in assigned_scope_names, (
                f"Expected {scope_name} to be assigned to Viewer role"
            )


@pytest.mark.anyio
async def test_system_scope_definitions_format(session):
    """Test that all system scope definitions follow the expected format."""
    for name, resource, action, description in SYSTEM_SCOPE_DEFINITIONS:
        # Name should contain resource and action
        assert ":" in name, f"Scope name should contain colon: {name}"

        # Description should be non-empty
        assert description, f"Scope {name} should have a description"

        # Resource and action should be non-empty
        assert resource, f"Scope {name} should have a resource"
        assert action, f"Scope {name} should have an action"
