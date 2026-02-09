"""RBAC scope and role seeding.

This module handles seeding of:
- System scopes: Built-in platform scopes (org, workspace, resource, RBAC admin)
- System roles: Admin, Editor, Viewer roles with their scope assignments
- Registry scopes: Auto-generated from registry actions during sync

Seeding is idempotent - existing scopes/roles are not duplicated.
"""

from typing import NamedTuple
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import PRESET_ROLE_SCOPES
from tracecat.db.models import Organization, Role, RoleScope, Scope
from tracecat.logger import logger

# =============================================================================
# System Scope Definitions
# =============================================================================

# These are the canonical system scopes that should exist in every deployment.


class ScopeDefinition(NamedTuple):
    name: str
    resource: str
    action: str
    description: str


SYSTEM_SCOPE_DEFINITIONS: list[ScopeDefinition] = [
    # Org-level scopes
    ScopeDefinition("org:read", "org", "read", "View organization settings"),
    ScopeDefinition("org:update", "org", "update", "Modify organization settings"),
    ScopeDefinition("org:delete", "org", "delete", "Delete organization"),
    # Org member management
    ScopeDefinition(
        "org:member:read", "org:member", "read", "List organization members"
    ),
    ScopeDefinition(
        "org:member:invite", "org:member", "invite", "Invite users to organization"
    ),
    ScopeDefinition(
        "org:member:remove", "org:member", "remove", "Remove users from organization"
    ),
    ScopeDefinition(
        "org:member:update", "org:member", "update", "Change member organization roles"
    ),
    # Billing
    ScopeDefinition(
        "org:billing:read", "org:billing", "read", "View billing information"
    ),
    ScopeDefinition("org:billing:manage", "org:billing", "manage", "Manage billing"),
    # RBAC administration
    ScopeDefinition(
        "org:rbac:read",
        "org:rbac",
        "read",
        "View roles, scopes, groups, and assignments",
    ),
    ScopeDefinition(
        "org:rbac:manage",
        "org:rbac",
        "manage",
        "Create/update/delete roles, scopes, groups, and manage assignments",
    ),
    # Workspace-level scopes
    ScopeDefinition("workspace:read", "workspace", "read", "View workspace settings"),
    ScopeDefinition("workspace:create", "workspace", "create", "Create workspaces"),
    ScopeDefinition(
        "workspace:update", "workspace", "update", "Modify workspace settings"
    ),
    ScopeDefinition("workspace:delete", "workspace", "delete", "Delete workspace"),
    # Workspace member management
    ScopeDefinition(
        "workspace:member:read", "workspace:member", "read", "List workspace members"
    ),
    ScopeDefinition(
        "workspace:member:invite",
        "workspace:member",
        "invite",
        "Add users to workspace",
    ),
    ScopeDefinition(
        "workspace:member:remove",
        "workspace:member",
        "remove",
        "Remove users from workspace",
    ),
    ScopeDefinition(
        "workspace:member:update",
        "workspace:member",
        "update",
        "Change member workspace roles",
    ),
    # Workflow scopes
    ScopeDefinition(
        "workflow:read", "workflow", "read", "View workflows and their details"
    ),
    ScopeDefinition("workflow:create", "workflow", "create", "Create new workflows"),
    ScopeDefinition(
        "workflow:update", "workflow", "update", "Modify existing workflows"
    ),
    ScopeDefinition("workflow:delete", "workflow", "delete", "Delete workflows"),
    ScopeDefinition("workflow:execute", "workflow", "execute", "Run/trigger workflows"),
    # Case scopes
    ScopeDefinition("case:read", "case", "read", "View cases"),
    ScopeDefinition("case:create", "case", "create", "Create new cases"),
    ScopeDefinition("case:update", "case", "update", "Modify existing cases"),
    ScopeDefinition("case:delete", "case", "delete", "Delete cases"),
    # Table scopes
    ScopeDefinition("table:read", "table", "read", "View tables"),
    ScopeDefinition("table:create", "table", "create", "Create new tables"),
    ScopeDefinition("table:update", "table", "update", "Modify existing tables"),
    ScopeDefinition("table:delete", "table", "delete", "Delete tables"),
    # Schedule scopes
    ScopeDefinition("schedule:read", "schedule", "read", "View schedules"),
    ScopeDefinition("schedule:create", "schedule", "create", "Create new schedules"),
    ScopeDefinition(
        "schedule:update", "schedule", "update", "Modify existing schedules"
    ),
    ScopeDefinition("schedule:delete", "schedule", "delete", "Delete schedules"),
    # Agent scopes
    ScopeDefinition("agent:read", "agent", "read", "View agents"),
    ScopeDefinition("agent:create", "agent", "create", "Create new agents"),
    ScopeDefinition("agent:update", "agent", "update", "Modify existing agents"),
    ScopeDefinition("agent:delete", "agent", "delete", "Delete agents"),
    ScopeDefinition("agent:execute", "agent", "execute", "Run/trigger agents"),
    # Secret scopes
    ScopeDefinition("secret:read", "secret", "read", "View secrets"),
    ScopeDefinition("secret:create", "secret", "create", "Create new secrets"),
    ScopeDefinition("secret:update", "secret", "update", "Modify existing secrets"),
    ScopeDefinition("secret:delete", "secret", "delete", "Delete secrets"),
    # Wildcard action scopes (for role assignments)
    ScopeDefinition(
        "action:*:execute", "action", "execute", "Execute any registry action"
    ),
    ScopeDefinition(
        "action:core.*:execute",
        "action:core",
        "execute",
        "Execute core registry actions",
    ),
]

# =============================================================================
# Preset Role Definitions
# =============================================================================

# Preset roles seeded per-organization.
# Slugs match the keys in PRESET_ROLE_SCOPES from scopes.py.


class RoleDefinition(NamedTuple):
    name: str
    description: str
    scopes: frozenset[str]


PRESET_ROLE_DEFINITIONS: dict[str, RoleDefinition] = {
    # slug → RoleDefinition(name, description, scopes)
    "workspace-viewer": RoleDefinition(
        "Viewer",
        "Read-only access to workspace resources",
        PRESET_ROLE_SCOPES["workspace-viewer"],
    ),
    "workspace-editor": RoleDefinition(
        "Editor",
        "Create and edit resources, no delete or admin access",
        PRESET_ROLE_SCOPES["workspace-editor"],
    ),
    "workspace-admin": RoleDefinition(
        "Admin",
        "Full workspace capabilities",
        PRESET_ROLE_SCOPES["workspace-admin"],
    ),
    "organization-owner": RoleDefinition(
        "Owner",
        "Full organization control",
        PRESET_ROLE_SCOPES["organization-owner"],
    ),
    "organization-admin": RoleDefinition(
        "Admin",
        "Organization admin without delete or billing manage",
        PRESET_ROLE_SCOPES["organization-admin"],
    ),
    "organization-member": RoleDefinition(
        "Member",
        "Basic organization membership",
        PRESET_ROLE_SCOPES["organization-member"],
    ),
}

PRESET_ROLE_SLUGS: frozenset[str] = frozenset(PRESET_ROLE_DEFINITIONS)


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
            "source": ScopeSource.PLATFORM,
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
    Uses upsert (ON CONFLICT DO NOTHING) for concurrency safety.

    Args:
        session: Database session
        action_key: The action key (e.g., "tools.okta.list_users")
        description: Optional description for the scope

    Returns:
        The created or existing Scope
    """
    scope_name = f"action:{action_key}:execute"
    scope_id = uuid4()

    # Use upsert for concurrency safety
    stmt = pg_insert(Scope).values(
        id=scope_id,
        name=scope_name,
        resource="action",
        action="execute",
        description=description or f"Execute {action_key} action",
        source=ScopeSource.PLATFORM,
        source_ref=action_key,
        organization_id=None,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["name"], index_where=Scope.organization_id.is_(None)
    )
    result = await session.execute(stmt)
    await session.flush()

    # Re-query to get the scope (whether newly inserted or already existing)
    select_stmt = select(Scope).where(
        Scope.name == scope_name, Scope.organization_id.is_(None)
    )
    select_result = await session.execute(select_stmt)
    scope = select_result.scalar_one_or_none()

    if result.rowcount and result.rowcount > 0:  # pyright: ignore[reportAttributeAccessIssue]
        logger.debug(
            "Registry scope created", scope_name=scope_name, action_key=action_key
        )

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
            "source": ScopeSource.PLATFORM,
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


async def seed_system_roles_for_org(
    session: AsyncSession,
    organization_id: UUID,
) -> int:
    """Seed system roles (Admin, Editor, Viewer) for an organization.

    Creates the system roles with their associated scopes if they don't exist.
    System roles are identified by their well-known slugs.
    Uses upsert (ON CONFLICT DO NOTHING) for concurrency safety.

    Args:
        session: Database session
        organization_id: The organization to seed roles for

    Returns:
        Number of roles created (0 if all already existed)
    """
    logger.info(
        "Seeding system roles for organization", organization_id=organization_id
    )

    # Get all system scope names -> IDs
    scope_stmt = select(Scope.id, Scope.name).where(Scope.organization_id.is_(None))
    scope_result = await session.execute(scope_stmt)
    scope_name_to_id: dict[str, UUID] = {
        name: id_ for id_, name in scope_result.tuples().all()
    }

    roles_created = 0

    # Prepare role values with pre-generated IDs
    role_values = []
    role_id_by_slug: dict[str, UUID] = {}
    for slug, (name, description, _) in PRESET_ROLE_DEFINITIONS.items():
        role_id = uuid4()
        role_id_by_slug[slug] = role_id
        role_values.append(
            {
                "id": role_id,
                "name": name,
                "slug": slug,
                "description": description,
                "organization_id": organization_id,
                "created_by": None,  # System-created
            }
        )

    # Bulk upsert roles - concurrency safe
    role_stmt = pg_insert(Role).values(role_values)
    role_stmt = role_stmt.on_conflict_do_nothing(
        index_elements=["organization_id", "slug"]
    )
    result = await session.execute(role_stmt)
    roles_created = result.rowcount if result.rowcount else 0  # pyright: ignore[reportAttributeAccessIssue]

    # Re-query to get actual role IDs (may differ if roles already existed)
    existing_roles_stmt = select(Role.id, Role.slug).where(
        Role.organization_id == organization_id,
        Role.slug.in_(PRESET_ROLE_DEFINITIONS),
    )
    existing_roles_result = await session.execute(existing_roles_stmt)
    actual_role_id_by_slug: dict[str | None, UUID] = {
        slug: role_id for role_id, slug in existing_roles_result.tuples().all()
    }

    # Link scopes to roles
    for slug, (_, _, scope_names) in PRESET_ROLE_DEFINITIONS.items():
        role_id = actual_role_id_by_slug.get(slug)
        if role_id is None:
            logger.warning(
                "Role not found after upsert",
                slug=slug,
                organization_id=organization_id,
            )
            continue

        role_scope_values = []
        for scope_name in scope_names:
            scope_id = scope_name_to_id.get(scope_name)
            if scope_id is None:
                logger.warning(
                    "Scope not found for system role",
                    scope_name=scope_name,
                    role_slug=slug,
                )
                continue
            role_scope_values.append({"role_id": role_id, "scope_id": scope_id})

        if role_scope_values:
            role_scope_stmt = pg_insert(RoleScope).values(role_scope_values)
            role_scope_stmt = role_scope_stmt.on_conflict_do_nothing()
            await session.execute(role_scope_stmt)

    await session.commit()
    logger.info(
        "System roles seeded for organization",
        organization_id=organization_id,
        roles_created=roles_created,
    )
    return roles_created


# =============================================================================
# Combined Seeding Functions
# =============================================================================


async def seed_system_roles_for_all_orgs(session: AsyncSession) -> dict[UUID, int]:
    """Seed system roles for all existing organizations.

    This is called during app startup to ensure existing organizations
    have all system roles. Idempotent - will not create duplicate roles.

    Args:
        session: Database session

    Returns:
        Dict mapping organization_id to number of roles created
    """
    logger.info("Seeding system roles for all organizations")

    # Get all organizations
    org_stmt = select(Organization.id)
    org_result = await session.execute(org_stmt)
    org_ids = [row[0] for row in org_result.all()]

    if not org_ids:
        logger.info("No organizations found, skipping system role seeding")
        return {}

    results: dict[UUID, int] = {}
    total_created = 0

    for org_id in org_ids:
        roles_created = await seed_system_roles_for_org(session, org_id)
        results[org_id] = roles_created
        total_created += roles_created

    logger.info(
        "System roles seeded for all organizations",
        num_orgs=len(org_ids),
        total_roles_created=total_created,
    )
    return results


async def seed_all_system_data(session: AsyncSession) -> dict[str, int]:
    """Seed all system scopes and roles.

    This is the main entry point for seeding during app startup.
    Seeds:
    1. System scopes (global, organization_id=NULL)
    2. System roles for all existing organizations

    Args:
        session: Database session

    Returns:
        Dict with counts of seeded items
    """
    logger.info("Starting system RBAC data seeding")

    # Seed system scopes first (roles reference scopes)
    scopes_created = await seed_system_scopes(session)

    # Seed system roles for all existing organizations
    org_role_results = await seed_system_roles_for_all_orgs(session)
    total_roles_created = sum(org_role_results.values())

    result = {
        "scopes_created": scopes_created,
        "roles_created": total_roles_created,
        "orgs_processed": len(org_role_results),
    }

    logger.info("System RBAC data seeding complete", **result)
    return result
