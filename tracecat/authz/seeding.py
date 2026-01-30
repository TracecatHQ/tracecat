"""RBAC scope and role seeding.

This module handles seeding of:
- System scopes: Built-in platform scopes (org, workspace, resource, RBAC admin)
- System roles: Admin, Editor, Viewer roles with their scope assignments
- Registry scopes: Auto-generated from registry actions during sync

Seeding is idempotent - existing scopes/roles are not duplicated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import (
    ADMIN_SCOPES,
    EDITOR_SCOPES,
    ORG_ADMIN_SCOPES,
    ORG_MEMBER_SCOPES,
    ORG_OWNER_SCOPES,
    VIEWER_SCOPES,
)
from tracecat.db.models import Role, RoleScope, Scope
from tracecat.logger import logger

if TYPE_CHECKING:
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

# =============================================================================
# Preset Role Definitions
# =============================================================================

# All preset role slugs
PRESET_ROLE_SLUGS: frozenset[str] = frozenset(
    {"owner", "admin", "editor", "viewer", "member"}
)

# Preset role definitions: (slug, name, description, scopes)
#
# Roles can be assigned at org level (workspace_id=NULL) or workspace level (workspace_id set).
# The same role can be used at both levels; the assignment context determines what access applies.
#
# Role hierarchy:
# - owner: Organization owner with full control (org-level only)
# - admin: Full administrative access (can be org-level or workspace-level)
# - editor: Create/edit access without admin capabilities (workspace-level)
# - viewer: Read-only access (workspace-level)
# - member: Basic org membership without workspace access (org-level only)
#
# Note: The "admin" role combines org admin and workspace admin scopes since
# it may be assigned at either level.
PRESET_ROLE_DEFINITIONS: list[tuple[str, str, str, frozenset[str]]] = [
    (
        "owner",
        "Owner",
        "Full organization control",
        ORG_OWNER_SCOPES,
    ),
    (
        "admin",
        "Admin",
        "Full administrative access at organization or workspace level",
        ADMIN_SCOPES | ORG_ADMIN_SCOPES,  # Combined for flexibility
    ),
    (
        "editor",
        "Editor",
        "Create and edit resources, no delete or admin access",
        EDITOR_SCOPES,
    ),
    (
        "viewer",
        "Viewer",
        "Read-only access to workspace resources",
        VIEWER_SCOPES,
    ),
    (
        "member",
        "Member",
        "Basic organization membership",
        ORG_MEMBER_SCOPES,
    ),
]


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


async def seed_system_roles_for_org(
    session: AsyncSession,
    organization_id: UUID,
) -> int:
    """Seed system roles (Admin, Editor, Viewer) for an organization.

    Creates the three system roles with their associated scopes if they don't exist.
    System roles are identified by their well-known slugs.

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
    scope_name_to_id: dict[str, UUID] = {name: id_ for id_, name in scope_result.all()}

    roles_created = 0

    for slug, name, description, scope_names in PRESET_ROLE_DEFINITIONS:
        # Check if role already exists
        existing_stmt = select(Role.id).where(
            Role.organization_id == organization_id,
            Role.slug == slug,
        )
        existing_result = await session.execute(existing_stmt)
        existing_role_id = existing_result.scalar_one_or_none()

        if existing_role_id is not None:
            logger.debug(
                "System role already exists",
                slug=slug,
                organization_id=organization_id,
            )
            continue

        # Create the role
        role = Role(
            id=uuid4(),
            name=name,
            slug=slug,
            description=description,
            organization_id=organization_id,
            created_by=None,  # System-created
        )
        session.add(role)
        await session.flush()  # Get the role ID

        # Link scopes to the role
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
            role_scope_values.append({"role_id": role.id, "scope_id": scope_id})

        if role_scope_values:
            role_scope_stmt = pg_insert(RoleScope).values(role_scope_values)
            role_scope_stmt = role_scope_stmt.on_conflict_do_nothing()
            await session.execute(role_scope_stmt)

        roles_created += 1
        logger.debug(
            "System role created",
            slug=slug,
            organization_id=organization_id,
            num_scopes=len(role_scope_values),
        )

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
    from tracecat.db.models import Organization

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
