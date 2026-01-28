"""RBAC scope and role seeding.

This module handles seeding of:
- System scopes: Built-in platform scopes (org, workspace, resource, RBAC admin)
- Registry scopes: Auto-generated from registry actions during sync
- System roles: Viewer, Editor, Admin roles per organization

Seeding is idempotent - existing scopes/roles are not duplicated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.authz.enums import ScopeSource, WorkspaceRole
from tracecat.authz.scopes import SYSTEM_ROLE_SCOPES
from tracecat.db.models import Role, RoleScope, Scope
from tracecat.logger import logger

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

# =============================================================================
# System Scope Definitions
# =============================================================================

# These are the canonical system scopes that should exist in every deployment.
# Format: (name, resource, action, description)

SYSTEM_SCOPE_DEFINITIONS: list[tuple[str, str, str, str]] = [
    # Org-level scopes
    ("org:read", "org", "read", "View organization settings"),
    ("org:update", "org", "update", "Modify organization settings"),
    ("org:delete", "org", "delete", "Delete organization"),
    # Org member management
    ("org:member:read", "org:member", "read", "List organization members"),
    ("org:member:invite", "org:member", "invite", "Invite users to organization"),
    ("org:member:remove", "org:member", "remove", "Remove users from organization"),
    ("org:member:update", "org:member", "update", "Change member organization roles"),
    # Billing
    ("org:billing:read", "org:billing", "read", "View billing information"),
    ("org:billing:manage", "org:billing", "manage", "Manage billing"),
    # RBAC administration
    (
        "org:rbac:read",
        "org:rbac",
        "read",
        "View roles, scopes, groups, and assignments",
    ),
    (
        "org:rbac:manage",
        "org:rbac",
        "manage",
        "Create/update/delete roles, scopes, groups, and manage assignments",
    ),
    # Workspace-level scopes
    ("workspace:read", "workspace", "read", "View workspace settings"),
    ("workspace:create", "workspace", "create", "Create workspaces"),
    ("workspace:update", "workspace", "update", "Modify workspace settings"),
    ("workspace:delete", "workspace", "delete", "Delete workspace"),
    # Workspace member management
    ("workspace:member:read", "workspace:member", "read", "List workspace members"),
    ("workspace:member:invite", "workspace:member", "invite", "Add users to workspace"),
    (
        "workspace:member:remove",
        "workspace:member",
        "remove",
        "Remove users from workspace",
    ),
    (
        "workspace:member:update",
        "workspace:member",
        "update",
        "Change member workspace roles",
    ),
    # Workflow scopes
    ("workflow:read", "workflow", "read", "View workflows and their details"),
    ("workflow:create", "workflow", "create", "Create new workflows"),
    ("workflow:update", "workflow", "update", "Modify existing workflows"),
    ("workflow:delete", "workflow", "delete", "Delete workflows"),
    ("workflow:execute", "workflow", "execute", "Run/trigger workflows"),
    # Case scopes
    ("case:read", "case", "read", "View cases"),
    ("case:create", "case", "create", "Create new cases"),
    ("case:update", "case", "update", "Modify existing cases"),
    ("case:delete", "case", "delete", "Delete cases"),
    # Table scopes
    ("table:read", "table", "read", "View tables"),
    ("table:create", "table", "create", "Create new tables"),
    ("table:update", "table", "update", "Modify existing tables"),
    ("table:delete", "table", "delete", "Delete tables"),
    # Schedule scopes
    ("schedule:read", "schedule", "read", "View schedules"),
    ("schedule:create", "schedule", "create", "Create new schedules"),
    ("schedule:update", "schedule", "update", "Modify existing schedules"),
    ("schedule:delete", "schedule", "delete", "Delete schedules"),
    # Agent scopes
    ("agent:read", "agent", "read", "View agents"),
    ("agent:create", "agent", "create", "Create new agents"),
    ("agent:update", "agent", "update", "Modify existing agents"),
    ("agent:delete", "agent", "delete", "Delete agents"),
    ("agent:execute", "agent", "execute", "Run/trigger agents"),
    # Secret scopes
    ("secret:read", "secret", "read", "View secrets"),
    ("secret:create", "secret", "create", "Create new secrets"),
    ("secret:update", "secret", "update", "Modify existing secrets"),
    ("secret:delete", "secret", "delete", "Delete secrets"),
    # Wildcard action scopes (for role assignments)
    ("action:*:execute", "action", "execute", "Execute any registry action"),
    (
        "action:core.*:execute",
        "action:core",
        "execute",
        "Execute core registry actions",
    ),
]

# System role definitions: (name, description)
SYSTEM_ROLE_DEFINITIONS: dict[WorkspaceRole, tuple[str, str]] = {
    WorkspaceRole.VIEWER: ("Viewer", "Read-only access to workspace resources"),
    WorkspaceRole.EDITOR: (
        "Editor",
        "Can create and edit resources, but not delete or manage workspace",
    ),
    WorkspaceRole.ADMIN: (
        "Admin",
        "Full workspace control including member management",
    ),
}


# =============================================================================
# Scope Seeding Functions
# =============================================================================


async def seed_system_scopes(session: AsyncSession) -> int:
    """Seed system scopes into the database.

    Uses PostgreSQL upsert (INSERT ... ON CONFLICT DO NOTHING) for idempotency.
    System scopes have organization_id=NULL and source='system'.

    Args:
        session: Database session

    Returns:
        Number of scopes inserted (0 if all already existed)
    """
    logger.info("Seeding system scopes", num_scopes=len(SYSTEM_SCOPE_DEFINITIONS))

    # Build values for bulk upsert
    values = [
        {
            "id": uuid4(),
            "name": name,
            "resource": resource,
            "action": action,
            "description": description,
            "source": ScopeSource.SYSTEM,
            "source_ref": None,
            "organization_id": None,
        }
        for name, resource, action, description in SYSTEM_SCOPE_DEFINITIONS
    ]

    # PostgreSQL upsert - do nothing on conflict (idempotent)
    stmt = pg_insert(Scope).values(values)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["name"], index_where=Scope.organization_id.is_(None)
    )

    result = await session.execute(stmt)
    await session.commit()

    inserted_count = result.rowcount if result.rowcount else 0  # pyright: ignore[reportAttributeAccessIssue]
    logger.info(
        "System scopes seeded",
        inserted=inserted_count,
        total=len(SYSTEM_SCOPE_DEFINITIONS),
    )
    return inserted_count


async def seed_registry_scope(
    session: AsyncSession,
    action_key: str,
    description: str | None = None,
) -> Scope | None:
    """Seed a single registry action scope.

    Creates a scope for `action:{action_key}:execute` if it doesn't exist.
    Registry scopes have organization_id=NULL and source='registry'.

    Args:
        session: Database session
        action_key: The action key (e.g., "tools.okta.list_users")
        description: Optional description for the scope

    Returns:
        The created or existing Scope, or None if upsert had no effect
    """
    scope_name = f"action:{action_key}:execute"

    # Check if scope already exists
    stmt = select(Scope).where(
        Scope.name == scope_name, Scope.organization_id.is_(None)
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        return existing

    # Create new scope
    scope = Scope(
        id=uuid4(),
        name=scope_name,
        resource="action",
        action="execute",
        description=description or f"Execute {action_key} action",
        source=ScopeSource.REGISTRY,
        source_ref=action_key,
        organization_id=None,
    )
    session.add(scope)
    await session.flush()

    logger.debug("Registry scope created", scope_name=scope_name, action_key=action_key)
    return scope


async def seed_registry_scopes_bulk(
    session: AsyncSession,
    action_keys: list[str],
) -> int:
    """Seed registry action scopes in bulk.

    Creates scopes for all action keys that don't already exist.
    Uses PostgreSQL upsert for efficiency.

    Args:
        session: Database session
        action_keys: List of action keys (e.g., ["tools.okta.list_users", "core.http_request"])

    Returns:
        Number of scopes inserted
    """
    if not action_keys:
        return 0

    logger.info("Seeding registry scopes", num_actions=len(action_keys))

    values = [
        {
            "id": uuid4(),
            "name": f"action:{key}:execute",
            "resource": "action",
            "action": "execute",
            "description": f"Execute {key} action",
            "source": ScopeSource.REGISTRY,
            "source_ref": key,
            "organization_id": None,
        }
        for key in action_keys
    ]

    stmt = pg_insert(Scope).values(values)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["name"], index_where=Scope.organization_id.is_(None)
    )

    result = await session.execute(stmt)
    inserted_count = result.rowcount if result.rowcount else 0  # pyright: ignore[reportAttributeAccessIssue]

    logger.info(
        "Registry scopes seeded", inserted=inserted_count, total=len(action_keys)
    )
    return inserted_count


# =============================================================================
# Role Seeding Functions
# =============================================================================


async def get_system_scope_ids(
    session: AsyncSession, scope_names: frozenset[str]
) -> dict[str, UUID]:
    """Get scope IDs for a set of scope names.

    For wildcard scopes (containing *), we need to find matching system scopes.
    For exact scopes, we do a direct lookup.

    Args:
        session: Database session
        scope_names: Set of scope names to look up

    Returns:
        Dict mapping scope name to scope ID
    """
    # Get all system scopes
    stmt = select(Scope).where(Scope.organization_id.is_(None))
    result = await session.execute(stmt)
    all_scopes = {s.name: s.id for s in result.scalars().all()}

    # For exact matches, return directly
    # For wildcards, the wildcard scope itself should exist (e.g., "action:*:execute")
    scope_ids = {}
    for name in scope_names:
        if name in all_scopes:
            scope_ids[name] = all_scopes[name]

    return scope_ids


async def seed_system_roles_for_org(
    session: AsyncSession,
    organization_id: UUID,
) -> int:
    """Seed system roles (Viewer, Editor, Admin) for an organization.

    Creates the three system roles if they don't exist, and assigns
    the appropriate scopes to each role based on SYSTEM_ROLE_SCOPES.

    Args:
        session: Database session
        organization_id: The organization to seed roles for

    Returns:
        Number of roles created (0 if all already existed)
    """
    logger.info(
        "Seeding system roles for organization", organization_id=str(organization_id)
    )

    created_count = 0

    for workspace_role, (role_name, description) in SYSTEM_ROLE_DEFINITIONS.items():
        # Check if role already exists
        stmt = select(Role).where(
            Role.organization_id == organization_id,
            Role.name == role_name,
        )
        result = await session.execute(stmt)
        existing_role = result.scalar_one_or_none()

        if existing_role:
            logger.debug(
                "System role already exists",
                role_name=role_name,
                organization_id=str(organization_id),
            )
            continue

        # Create the role
        role = Role(
            id=uuid4(),
            name=role_name,
            description=description,
            organization_id=organization_id,
            is_system=True,
            created_by=None,
        )
        session.add(role)
        await session.flush()

        # Get scope IDs for this role
        scope_names = SYSTEM_ROLE_SCOPES[workspace_role]
        scope_ids = await get_system_scope_ids(session, scope_names)

        # Create role-scope associations
        for scope_id in scope_ids.values():
            role_scope = RoleScope(role_id=role.id, scope_id=scope_id)
            session.add(role_scope)

        logger.debug(
            "System role created",
            role_name=role_name,
            organization_id=str(organization_id),
            num_scopes=len(scope_ids),
        )
        created_count += 1

    await session.commit()
    logger.info(
        "System roles seeded for organization",
        organization_id=str(organization_id),
        created=created_count,
    )
    return created_count


async def seed_system_roles_for_all_orgs(session: AsyncSession) -> int:
    """Seed system roles for all existing organizations.

    This should be called during startup to ensure all orgs have system roles.

    Args:
        session: Database session

    Returns:
        Total number of roles created across all orgs
    """
    from tracecat.db.models import Organization

    # Get all organization IDs
    stmt = select(Organization.id)
    result = await session.execute(stmt)
    org_ids = list(result.scalars().all())

    logger.info("Seeding system roles for all organizations", num_orgs=len(org_ids))

    total_created = 0
    for org_id in org_ids:
        created = await seed_system_roles_for_org(session, org_id)
        total_created += created

    logger.info(
        "System roles seeded for all organizations", total_created=total_created
    )
    return total_created


# =============================================================================
# Combined Seeding Functions
# =============================================================================


async def seed_all_system_data(session: AsyncSession) -> dict[str, int]:
    """Seed all system scopes and roles.

    This is the main entry point for seeding during app startup.
    It seeds system scopes first, then system roles for all orgs.

    Args:
        session: Database session

    Returns:
        Dict with counts of seeded items
    """
    logger.info("Starting system RBAC data seeding")

    # First, seed system scopes (roles depend on these)
    scopes_created = await seed_system_scopes(session)

    # Then, seed system roles for all organizations
    roles_created = await seed_system_roles_for_all_orgs(session)

    result = {
        "scopes_created": scopes_created,
        "roles_created": roles_created,
    }

    logger.info("System RBAC data seeding complete", **result)
    return result
