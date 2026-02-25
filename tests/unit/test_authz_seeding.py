"""Tests for RBAC scope seeding."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from tracecat.authz.enums import ScopeSource
from tracecat.authz.seeding import (
    PRESET_ROLE_DEFINITIONS,
    SYSTEM_SCOPE_DEFINITIONS,
    seed_registry_scopes,
    seed_system_roles_for_all_orgs,
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
            Scope.source == ScopeSource.PLATFORM,
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
            Scope.source == ScopeSource.PLATFORM,
            Scope.organization_id.is_(None),
        )
    )
    scopes = result.scalars().all()
    assert len(scopes) == len(SYSTEM_SCOPE_DEFINITIONS)


@pytest.mark.anyio
async def test_seed_registry_scopes(session):
    """Test bulk seeding of registry scopes."""
    action_keys = [
        "tools.okta.list_users",
        "tools.okta.create_user",
        "tools.zendesk.create_ticket",
        "core.http_request",
    ]

    inserted_count = await seed_registry_scopes(session, action_keys)
    await session.commit()

    assert inserted_count == len(action_keys)

    # Verify scopes exist
    result = await session.execute(
        select(Scope).where(
            Scope.source == ScopeSource.PLATFORM,
            Scope.organization_id.is_(None),
        )
    )
    scopes = result.scalars().all()
    assert len(scopes) >= len(action_keys)

    scope_names = {s.name for s in scopes}
    for key in action_keys:
        assert f"action:{key}:execute" in scope_names

    action_scope_names = [f"action:{key}:execute" for key in action_keys]
    custom_scope_result = await session.execute(
        select(func.count(Scope.id)).where(
            Scope.source == ScopeSource.CUSTOM,
            Scope.name.in_(action_scope_names),
        )
    )
    assert custom_scope_result.scalar_one() == 0


@pytest.mark.anyio
async def test_seed_registry_scopes_idempotent(session):
    """Test that bulk seeding is idempotent."""
    action_keys = ["tools.test.action1", "tools.test.action2"]

    # First seed
    first_count = await seed_registry_scopes(session, action_keys)
    await session.commit()
    assert first_count == len(action_keys)

    # Second seed
    second_count = await seed_registry_scopes(session, action_keys)
    await session.commit()
    assert second_count == 0


@pytest.mark.anyio
async def test_seed_registry_scopes_empty(session):
    """Test bulk seeding with empty list."""
    inserted_count = await seed_registry_scopes(session, [])
    assert inserted_count == 0


@pytest.mark.anyio
async def test_seed_system_roles_for_all_orgs_creates_roles_and_links(session):
    """Seed preset roles for all orgs and link expected system scopes."""
    # Ensure scope IDs exist for role->scope links.
    await seed_system_scopes(session)

    # Add an extra org so the function processes multiple orgs in one call.
    extra_org = Organization(
        id=uuid4(),
        name="Extra test org",
        slug=f"extra-test-org-{uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(extra_org)
    await session.flush()

    # Capture target org IDs in this isolated session.
    org_result = await session.execute(select(Organization.id))
    org_ids = {org_id for (org_id,) in org_result.tuples().all()}

    processed_org_ids = await seed_system_roles_for_all_orgs(session)
    assert set(processed_org_ids) == org_ids

    roles_result = await session.execute(
        select(Role.id, Role.organization_id, Role.slug).where(
            Role.organization_id.in_(org_ids),
            Role.slug.in_(PRESET_ROLE_DEFINITIONS),
        )
    )
    roles = roles_result.tuples().all()
    assert len(roles) == len(org_ids) * len(PRESET_ROLE_DEFINITIONS)

    role_scope_count_stmt = (
        select(Role.slug, func.count(RoleScope.scope_id))
        .select_from(Role)
        .join(RoleScope, RoleScope.role_id == Role.id)
        .where(
            Role.organization_id.in_(org_ids),
            Role.slug.in_(PRESET_ROLE_DEFINITIONS),
        )
        .group_by(Role.slug)
    )
    role_scope_count_result = await session.execute(role_scope_count_stmt)
    role_scope_counts = dict(role_scope_count_result.tuples().all())
    expected_org_count = len(org_ids)
    for slug, role_def in PRESET_ROLE_DEFINITIONS.items():
        assert role_scope_counts[slug] == len(role_def.scopes) * expected_org_count


@pytest.mark.anyio
async def test_seed_system_roles_for_all_orgs_idempotent(session):
    """Running role seeding twice should not create duplicate roles."""
    await seed_system_scopes(session)

    first = await seed_system_roles_for_all_orgs(session)
    second = await seed_system_roles_for_all_orgs(session)
    assert first
    assert set(first) == set(second)

    org_count_result = await session.execute(select(func.count(Organization.id)))
    org_count = org_count_result.scalar_one()
    role_count_result = await session.execute(
        select(func.count(Role.id)).where(Role.slug.in_(PRESET_ROLE_DEFINITIONS))
    )
    role_count = role_count_result.scalar_one()
    assert role_count == org_count * len(PRESET_ROLE_DEFINITIONS)


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


@pytest.mark.anyio
async def test_system_scope_definitions_cover_all_preset_role_scopes(session):
    """All preset role scopes must exist in system scope definitions."""
    system_scope_names = {name for name, _, _, _ in SYSTEM_SCOPE_DEFINITIONS}
    preset_scope_names = {
        scope_name
        for role_def in PRESET_ROLE_DEFINITIONS.values()
        for scope_name in role_def.scopes
    }

    missing = preset_scope_names - system_scope_names
    assert not missing, f"Missing system scope definitions for preset scopes: {missing}"
