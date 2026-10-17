"""Tests for RBAC scope seeding."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from tracecat.authz.enums import ScopeSource
from tracecat.authz.seeding import (
    PRESET_ROLE_DEFINITIONS,
    SYSTEM_SCOPE_DEFINITIONS,
    seed_registry_scopes,
    seed_system_roles_for_all_orgs,
    seed_system_roles_for_org,
    seed_system_scopes,
)
from tracecat.db.models import Organization, Role, RoleScope, Scope


@pytest.mark.anyio
async def test_seed_system_scopes(session):
    """System scopes are seeded once; a second run inserts nothing."""
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

    assert await seed_system_scopes(session) == 0
    rerun_result = await session.execute(
        select(func.count(Scope.id)).where(
            Scope.source == ScopeSource.PLATFORM,
            Scope.organization_id.is_(None),
        )
    )
    assert rerun_result.scalar_one() == len(SYSTEM_SCOPE_DEFINITIONS)


@pytest.mark.anyio
async def test_seed_registry_scopes(session):
    """Registry scopes are bulk-seeded once as platform scopes."""
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

    assert await seed_registry_scopes(session, action_keys) == 0


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

    # Re-running must not duplicate roles.
    assert set(await seed_system_roles_for_all_orgs(session)) == org_ids
    rerun_roles = await session.execute(
        select(func.count(Role.id)).where(
            Role.organization_id.in_(org_ids),
            Role.slug.in_(PRESET_ROLE_DEFINITIONS),
        )
    )
    assert rerun_roles.scalar_one() == len(org_ids) * len(PRESET_ROLE_DEFINITIONS)


@pytest.mark.anyio
async def test_seeding_reconciles_preset_role_links_to_their_definitions(session):
    """Reseeding adds missing preset links, removes extra ones, and leaves custom roles alone.

    An older app version re-seeds links a newer definition dropped, such as
    agent scopes on organization-member, so each startup must remove them.
    """
    await seed_system_scopes(session)
    org = Organization(
        id=uuid4(),
        name="Reconcile org",
        slug=f"reconcile-org-{uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.flush()
    await seed_system_roles_for_org(session, org.id)

    scope_ids = dict(
        (
            await session.execute(
                select(Scope.name, Scope.id).where(Scope.organization_id.is_(None))
            )
        )
        .tuples()
        .all()
    )
    role_ids = dict(
        (
            await session.execute(
                select(Role.slug, Role.id).where(Role.organization_id == org.id)
            )
        )
        .tuples()
        .all()
    )
    custom_role = Role(id=uuid4(), name="Agent runner", organization_id=org.id)
    session.add(custom_role)
    await session.flush()
    session.add_all(
        [
            # Stale links an older definition carried.
            RoleScope(
                role_id=role_ids["organization-member"],
                scope_id=scope_ids["agent:read"],
            ),
            RoleScope(
                role_id=role_ids["organization-member"],
                scope_id=scope_ids["agent:execute"],
            ),
            RoleScope(
                role_id=role_ids["workspace-viewer"],
                scope_id=scope_ids["agent:delete"],
            ),
            RoleScope(role_id=custom_role.id, scope_id=scope_ids["agent:execute"]),
        ]
    )
    await session.execute(
        delete(RoleScope).where(
            RoleScope.role_id == role_ids["workspace-editor"],
            RoleScope.scope_id == scope_ids["agent:execute"],
        )
    )
    await session.flush()

    await seed_system_roles_for_org(session, org.id)

    links = (
        await session.execute(
            select(Role.slug, Role.id, Scope.name)
            .join(RoleScope, RoleScope.role_id == Role.id)
            .join(Scope, Scope.id == RoleScope.scope_id)
            .where(Role.organization_id == org.id)
        )
    ).tuples()
    preset_links: dict[str, set[str]] = {}
    custom_links: set[str] = set()
    for slug, role_id, scope_name in links:
        if role_id == custom_role.id:
            custom_links.add(scope_name)
        else:
            preset_links.setdefault(slug, set()).add(scope_name)
    assert preset_links == {
        slug: set(role_def.scopes) for slug, role_def in PRESET_ROLE_DEFINITIONS.items()
    }
    assert custom_links == {"agent:execute"}


def test_system_scope_definitions_format():
    """Test that all system scope definitions follow the expected format."""
    for name, resource, action, description in SYSTEM_SCOPE_DEFINITIONS:
        # Name should contain resource and action
        assert ":" in name, f"Scope name should contain colon: {name}"

        # Description should be non-empty
        assert description, f"Scope {name} should have a description"

        # Resource and action should be non-empty
        assert resource, f"Scope {name} should have a resource"
        assert action, f"Scope {name} should have an action"


def test_system_scope_definitions_cover_all_preset_role_scopes():
    """All preset role scopes must exist in system scope definitions."""
    system_scope_names = {name for name, _, _, _ in SYSTEM_SCOPE_DEFINITIONS}
    preset_scope_names = {
        scope_name
        for role_def in PRESET_ROLE_DEFINITIONS.values()
        for scope_name in role_def.scopes
    }

    missing = preset_scope_names - system_scope_names
    assert not missing, f"Missing system scope definitions for preset scopes: {missing}"
