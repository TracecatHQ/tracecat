"""Tests for the canonical grant resolver.

These pin the defining invariant: the scope ceiling is decided from live
database state, never from the cached ``Role.scopes`` snapshot. Each test
deliberately puts cached and database state in conflict, so replacing the
live query with ``Role.scopes`` flips the outcome and fails the test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.seeding import seed_system_scopes
from tracecat.authz.service import resolve_grantable_role
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    Organization,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]

OWNER_SCOPE = "org:owner:assign"
ADMIN_SCOPE = "org:member:read"


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Test Org",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def granter_user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"granter-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()
    session.add(OrganizationMembership(user_id=user.id, organization_id=org.id))
    await session.commit()
    return user


@pytest.fixture
async def seeded(session: AsyncSession) -> None:
    await seed_system_scopes(session)


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    ws = Workspace(
        id=uuid.uuid4(),
        name=f"ws-{uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    session.add(ws)
    await session.commit()
    return ws


async def _scope(session: AsyncSession, name: str) -> Scope:
    result = await session.execute(select(Scope).where(Scope.name == name))
    return result.scalars().one()


async def _make_role(
    session: AsyncSession, org: Organization, name: str, scope_names: list[str]
) -> DBRole:
    role = DBRole(id=uuid.uuid4(), name=name, slug=None, organization_id=org.id)
    session.add(role)
    await session.flush()
    for scope_name in scope_names:
        scope = await _scope(session, scope_name)
        session.add(RoleScope(role_id=role.id, scope_id=scope.id))
    await session.commit()
    return role


async def _assign(
    session: AsyncSession,
    org: Organization,
    user: User,
    role: DBRole,
    workspace_id: uuid.UUID | None = None,
) -> None:
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace_id,
            role_id=role.id,
        )
    )
    await session.commit()


async def _assign_via_group(
    session: AsyncSession,
    org: Organization,
    user: User,
    role: DBRole,
    workspace_id: uuid.UUID | None = None,
) -> None:
    group = Group(
        id=uuid.uuid4(),
        name=f"group-{uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            role_id=role.id,
            workspace_id=workspace_id,
        )
    )
    await session.commit()


def _role_claiming(user: User, org: Organization, scopes: set[str]) -> Role:
    """Build a request role whose cached scopes may disagree with the database."""
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset(scopes),
    )


async def test_denies_when_cached_scopes_exceed_database(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    seeded: None,
) -> None:
    """A stale cache claiming owner scope must not authorize an owner grant.

    Models a just-demoted owner: the request snapshot still carries the owner
    scope, but the database says they only hold the admin scope.
    """
    admin_role = await _make_role(session, org, "Admin", [ADMIN_SCOPE])
    await _assign(session, org, granter_user, admin_role)
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    # Cached snapshot claims MORE than the database grants.
    granter = _role_claiming(granter_user, org, {OWNER_SCOPE, ADMIN_SCOPE})

    with pytest.raises(
        TracecatAuthorizationError,
        match="Cannot grant scopes not held by the caller",
    ):
        await resolve_grantable_role(session, granter, org.id, target.id)


async def test_allows_when_database_exceeds_cached_scopes(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    seeded: None,
) -> None:
    """A stale cache missing owner scope must not block an owner grant.

    Models a just-promoted owner: the database already grants the owner scope
    while the request snapshot predates the promotion.
    """
    owner_role = await _make_role(session, org, "Owner", [OWNER_SCOPE, ADMIN_SCOPE])
    await _assign(session, org, granter_user, owner_role)
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    # Cached snapshot claims LESS than the database grants.
    granter = _role_claiming(granter_user, org, {ADMIN_SCOPE})

    resolved = await resolve_grantable_role(session, granter, org.id, target.id)
    assert resolved.id == target.id


async def test_group_membership_counts_toward_ceiling(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    seeded: None,
) -> None:
    """Privileges held solely via group membership authorize a grant.

    The cached snapshot claims nothing, so this also pins that the live
    query unions the group path, not just direct assignments.
    """
    owner_role = await _make_role(session, org, "Owner", [OWNER_SCOPE])
    await _assign_via_group(session, org, granter_user, owner_role)
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    granter = _role_claiming(granter_user, org, set())

    resolved = await resolve_grantable_role(session, granter, org.id, target.id)
    assert resolved.id == target.id


async def test_workspace_scoped_assignment_counts_in_workspace_context(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    workspace: Workspace,
    seeded: None,
) -> None:
    """A workspace-scoped direct assignment applies when granting in that workspace."""
    owner_role = await _make_role(session, org, "Owner", [OWNER_SCOPE])
    await _assign(session, org, granter_user, owner_role, workspace_id=workspace.id)
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    granter = Role(
        type="user",
        user_id=granter_user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        service_id="tracecat-api",
        scopes=frozenset(),
    )

    resolved = await resolve_grantable_role(session, granter, org.id, target.id)
    assert resolved.id == target.id


async def test_workspace_scoped_assignment_ignored_in_org_context(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    workspace: Workspace,
    seeded: None,
) -> None:
    """A workspace-scoped group grant does not raise the org-context ceiling.

    The cached snapshot claims the owner scope, so passing requires the live
    query to correctly exclude workspace-scoped assignments when the granter
    is operating without a workspace context.
    """
    owner_role = await _make_role(session, org, "Owner", [OWNER_SCOPE])
    await _assign_via_group(
        session, org, granter_user, owner_role, workspace_id=workspace.id
    )
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    granter = _role_claiming(granter_user, org, {OWNER_SCOPE})

    with pytest.raises(
        TracecatAuthorizationError,
        match="Cannot grant scopes not held by the caller",
    ):
        await resolve_grantable_role(session, granter, org.id, target.id)


async def test_service_role_falls_back_to_cached_scopes(
    session: AsyncSession,
    org: Organization,
    seeded: None,
) -> None:
    """Service principals have no user row; the ceiling uses cached scopes."""
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    granter = Role(
        type="service",
        user_id=None,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({OWNER_SCOPE}),
    )

    resolved = await resolve_grantable_role(session, granter, org.id, target.id)
    assert resolved.id == target.id


async def test_service_role_denied_beyond_cached_scopes(
    session: AsyncSession,
    org: Organization,
    seeded: None,
) -> None:
    """The cached-scope fallback still enforces the ceiling for service roles."""
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])

    granter = Role(
        type="service",
        user_id=None,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({ADMIN_SCOPE}),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match="Cannot grant scopes not held by the caller",
    ):
        await resolve_grantable_role(session, granter, org.id, target.id)


async def test_rejects_role_from_another_organization(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    seeded: None,
) -> None:
    """Ownership is validated: a role in another org is not resolvable."""
    other_org = Organization(
        id=uuid.uuid4(),
        name="Other Org",
        slug=f"other-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.commit()
    foreign_role = await _make_role(session, other_org, "Foreign", [ADMIN_SCOPE])

    granter = _role_claiming(granter_user, org, {OWNER_SCOPE, ADMIN_SCOPE})

    with pytest.raises(TracecatNotFoundError):
        await resolve_grantable_role(session, granter, org.id, foreign_role.id)


async def test_superuser_bypasses_ceiling(
    session: AsyncSession,
    org: Organization,
    granter_user: User,
    seeded: None,
) -> None:
    """Platform superusers are not bound by the ceiling."""
    target = await _make_role(session, org, "Owner Target", [OWNER_SCOPE])
    granter = Role(
        type="user",
        user_id=granter_user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        is_platform_superuser=True,
        scopes=frozenset(),
    )

    resolved = await resolve_grantable_role(session, granter, org.id, target.id)
    assert resolved.id == target.id
