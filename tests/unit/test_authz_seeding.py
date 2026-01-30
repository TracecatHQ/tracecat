"""Tests for RBAC scope seeding."""

import pytest
from sqlalchemy import select

from tracecat.authz.enums import ScopeSource
from tracecat.authz.seeding import (
    SYSTEM_SCOPE_DEFINITIONS,
    seed_registry_scope,
    seed_registry_scopes_bulk,
    seed_system_scopes,
)
from tracecat.db.models import Scope


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
