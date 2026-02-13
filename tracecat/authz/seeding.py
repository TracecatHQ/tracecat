"""RBAC scope and role seeding.

This module handles seeding of:
- System scopes: Built-in platform scopes (org, workspace, resource, RBAC admin)
- System roles: Admin, Editor, Viewer roles with their scope assignments
- Registry scopes: Auto-generated from registry actions during sync

Seeding is idempotent - existing scopes/roles are not duplicated.
"""

from typing import NamedTuple, TypedDict
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
    ScopeDefinition("org:billing:update", "org:billing", "update", "Manage billing"),
    # RBAC administration
    ScopeDefinition(
        "org:rbac:read",
        "org:rbac",
        "read",
        "View roles, scopes, groups, and assignments",
    ),
    ScopeDefinition(
        "org:rbac:create",
        "org:rbac",
        "create",
        "Create roles, scopes, groups, and assignments",
    ),
    ScopeDefinition(
        "org:rbac:update",
        "org:rbac",
        "update",
        "Update roles, groups, and assignments",
    ),
    ScopeDefinition(
        "org:rbac:delete",
        "org:rbac",
        "delete",
        "Delete roles, scopes, groups, and assignments",
    ),
    # Org settings management
    ScopeDefinition(
        "org:settings:read",
        "org:settings",
        "read",
        "View organization settings and configuration",
    ),
    ScopeDefinition(
        "org:settings:update",
        "org:settings",
        "update",
        "Manage organization settings and configuration",
    ),
    ScopeDefinition(
        "org:settings:delete",
        "org:settings",
        "delete",
        "Delete organization settings and configuration",
    ),
    # Registry administration
    ScopeDefinition(
        "org:registry:read",
        "org:registry",
        "read",
        "View organization registry repositories and versions",
    ),
    ScopeDefinition(
        "org:registry:create",
        "org:registry",
        "create",
        "Create organization registry repositories and versions",
    ),
    ScopeDefinition(
        "org:registry:update",
        "org:registry",
        "update",
        "Update organization registry repositories and versions",
    ),
    ScopeDefinition(
        "org:registry:delete",
        "org:registry",
        "delete",
        "Delete organization registry repositories and versions",
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
    ScopeDefinition(
        "workflow:terminate", "workflow", "terminate", "Stop running workflows"
    ),
    # Integration scopes
    ScopeDefinition(
        "integration:read",
        "integration",
        "read",
        "View integrations and provider metadata",
    ),
    ScopeDefinition(
        "integration:create",
        "integration",
        "create",
        "Create integrations and provider configurations",
    ),
    ScopeDefinition(
        "integration:update",
        "integration",
        "update",
        "Manage integration configuration and connections",
    ),
    ScopeDefinition(
        "integration:delete",
        "integration",
        "delete",
        "Delete integrations and provider configurations",
    ),
    # Case scopes
    ScopeDefinition("case:read", "case", "read", "View cases"),
    ScopeDefinition("case:create", "case", "create", "Create new cases"),
    ScopeDefinition("case:update", "case", "update", "Modify existing cases"),
    ScopeDefinition("case:delete", "case", "delete", "Delete cases"),
    # Inbox scopes
    ScopeDefinition("inbox:read", "inbox", "read", "View inbox items"),
    # Table scopes
    ScopeDefinition("table:read", "table", "read", "View tables"),
    ScopeDefinition("table:create", "table", "create", "Create new tables"),
    ScopeDefinition("table:update", "table", "update", "Modify existing tables"),
    ScopeDefinition("table:delete", "table", "delete", "Delete tables"),
    # Tag scopes
    ScopeDefinition("tag:read", "tag", "read", "View tags"),
    ScopeDefinition("tag:create", "tag", "create", "Create new tags"),
    ScopeDefinition("tag:update", "tag", "update", "Modify existing tags"),
    ScopeDefinition("tag:delete", "tag", "delete", "Delete tags"),
    # Variable scopes
    ScopeDefinition("variable:read", "variable", "read", "View variables"),
    ScopeDefinition("variable:create", "variable", "create", "Create new variables"),
    ScopeDefinition("variable:update", "variable", "update", "Modify variables"),
    ScopeDefinition("variable:delete", "variable", "delete", "Delete variables"),
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
    # Organization secret scopes
    ScopeDefinition("org:secret:read", "org:secret", "read", "View org-level secrets"),
    ScopeDefinition(
        "org:secret:create", "org:secret", "create", "Create org-level secrets"
    ),
    ScopeDefinition(
        "org:secret:update", "org:secret", "update", "Modify org-level secrets"
    ),
    ScopeDefinition(
        "org:secret:delete", "org:secret", "delete", "Delete org-level secrets"
    ),
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
        "Workspace Viewer",
        "Read-only access to workspace resources",
        PRESET_ROLE_SCOPES["workspace-viewer"],
    ),
    "workspace-editor": RoleDefinition(
        "Workspace Editor",
        "Create and edit resources, no delete or admin access",
        PRESET_ROLE_SCOPES["workspace-editor"],
    ),
    "workspace-admin": RoleDefinition(
        "Workspace Admin",
        "Full workspace capabilities",
        PRESET_ROLE_SCOPES["workspace-admin"],
    ),
    "organization-owner": RoleDefinition(
        "Organization Owner",
        "Full organization control",
        PRESET_ROLE_SCOPES["organization-owner"],
    ),
    "organization-admin": RoleDefinition(
        "Organization Admin",
        "Organization admin without delete or billing update",
        PRESET_ROLE_SCOPES["organization-admin"],
    ),
    "organization-member": RoleDefinition(
        "Organization Member",
        "Basic organization membership",
        PRESET_ROLE_SCOPES["organization-member"],
    ),
}

_CUSTOM_SCOPE_BATCH_ROWS = 5_000


class ScopeInsertRow(TypedDict):
    id: UUID
    name: str
    resource: str
    action: str
    description: str
    source: ScopeSource
    source_ref: str
    organization_id: UUID | None


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

    inserted_count = result.rowcount or 0  # pyright: ignore[reportAttributeAccessIssue]
    logger.info(
        "System scopes seeded",
        inserted=inserted_count,
        total=len(SYSTEM_SCOPE_DEFINITIONS),
    )
    return inserted_count


async def seed_registry_scopes(
    session: AsyncSession,
    action_keys: list[str],
) -> int:
    """Seed registry scopes.

    Current behavior has two explicit steps:
    1. Seed platform registry scopes.
    2. Seed custom registry scopes
    """
    platform_inserted = await _seed_platform_registry_scopes(
        session,
        action_keys,
    )
    custom_inserted = await _seed_custom_registry_scopes(session, action_keys)
    return platform_inserted + custom_inserted


async def _seed_platform_registry_scopes(
    session: AsyncSession,
    action_keys: list[str],
) -> int:
    """Seed platform registry action scopes in bulk."""
    if not action_keys:
        return 0

    logger.info(
        "Seeding registry scopes",
        num_actions=len(action_keys),
    )

    values = [
        _build_registry_scope_row(
            action_key=key,
            source=ScopeSource.PLATFORM,
            organization_id=None,
        )
        for key in action_keys
    ]

    return await _upsert_registry_scope_rows(
        session=session,
        values=values,
        source=ScopeSource.PLATFORM,
    )


async def _seed_custom_registry_scopes(
    session: AsyncSession,
    action_keys: list[str],
) -> int:
    """Seed custom registry scopes for all organizations using chunked upserts."""
    if not action_keys:
        return 0

    org_stmt = select(Organization.id)
    org_result = await session.execute(org_stmt)
    org_ids = [org_id for (org_id,) in org_result.tuples().all()]
    if not org_ids:
        return 0

    logger.info(
        "Seeding registry scopes",
        num_actions=len(action_keys),
        source=ScopeSource.CUSTOM.value,
        num_organizations=len(org_ids),
    )

    inserted_count = 0
    batch_values: list[ScopeInsertRow] = []
    for org_id in org_ids:
        for key in action_keys:
            batch_values.append(
                _build_registry_scope_row(
                    action_key=key,
                    source=ScopeSource.CUSTOM,
                    organization_id=org_id,
                )
            )
            if len(batch_values) >= _CUSTOM_SCOPE_BATCH_ROWS:
                inserted_count += await _upsert_registry_scope_rows(
                    session=session,
                    values=batch_values,
                    source=ScopeSource.CUSTOM,
                )
                batch_values.clear()

    if batch_values:
        inserted_count += await _upsert_registry_scope_rows(
            session=session,
            values=batch_values,
            source=ScopeSource.CUSTOM,
        )

    logger.info(
        "Registry scopes seeded",
        inserted=inserted_count,
        total=len(org_ids) * len(action_keys),
        source=ScopeSource.CUSTOM.value,
    )
    return inserted_count


def _build_registry_scope_row(
    *, action_key: str, source: ScopeSource, organization_id: UUID | None
) -> ScopeInsertRow:
    """Build a single scope insert row for a registry action key."""
    return {
        "id": uuid4(),
        "name": f"action:{action_key}:execute",
        "resource": "action",
        "action": "execute",
        "description": f"Execute {action_key} action",
        "source": source,
        "source_ref": action_key,
        "organization_id": organization_id,
    }


async def _upsert_registry_scope_rows(
    *,
    session: AsyncSession,
    values: list[ScopeInsertRow],
    source: ScopeSource,
) -> int:
    """Insert scope rows with conflict handling for platform vs org-scoped scopes."""
    if not values:
        return 0
    stmt = pg_insert(Scope).values(values)
    if source == ScopeSource.PLATFORM:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["name"], index_where=Scope.organization_id.is_(None)
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["organization_id", "name"])

    result = await session.execute(stmt)
    inserted_count = result.rowcount or 0  # pyright: ignore[reportAttributeAccessIssue]

    logger.info(
        "Registry scopes seeded",
        inserted=inserted_count,
        total=len(values),
        source=source.value,
    )
    return inserted_count


# =============================================================================
# Role Seeding Functions
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

    # 1) Upsert all preset roles for all orgs in one bulk query.
    role_values = []
    for org_id in org_ids:
        for slug, role_def in PRESET_ROLE_DEFINITIONS.items():
            role_values.append(
                {
                    "id": uuid4(),
                    "name": role_def.name,
                    "slug": slug,
                    "description": role_def.description,
                    "organization_id": org_id,
                    "created_by": None,
                }
            )

    role_insert_stmt = pg_insert(Role).values(role_values)
    role_insert_stmt = role_insert_stmt.on_conflict_do_update(
        index_elements=["organization_id", "slug"],
        set_={
            "name": role_insert_stmt.excluded.name,
            "description": role_insert_stmt.excluded.description,
        },
    ).returning(Role.organization_id)
    role_insert_result = await session.execute(role_insert_stmt)

    # Return shape: {org_id: roles_upserted_for_org} (includes both inserts and updates).
    results: dict[UUID, int] = dict.fromkeys(org_ids, 0)
    for (organization_id,) in role_insert_result.tuples().all():
        results[organization_id] += 1

    # 2) Fetch all relevant global scopes once.
    scope_stmt = select(Scope.id, Scope.name).where(Scope.organization_id.is_(None))
    scope_result = await session.execute(scope_stmt)
    scope_id_by_name: dict[str, UUID] = {
        scope_name: scope_id for scope_id, scope_name in scope_result.tuples().all()
    }

    # 3) Fetch all preset roles for those orgs and bulk insert role-scope links.
    role_stmt = select(Role.id, Role.slug).where(
        Role.organization_id.in_(org_ids),
        Role.slug.in_(PRESET_ROLE_DEFINITIONS),
    )
    role_result = await session.execute(role_stmt)

    role_scope_values = []
    for role_id, role_slug in role_result.tuples().all():
        if role_slug is None:
            continue
        role_def = PRESET_ROLE_DEFINITIONS.get(role_slug)
        if role_def is None:
            continue
        for scope_name in role_def.scopes:
            scope_id = scope_id_by_name.get(scope_name)
            if scope_id is None:
                logger.warning(
                    "Scope not found for system role",
                    scope_name=scope_name,
                    role_slug=role_slug,
                )
                continue
            role_scope_values.append({"role_id": role_id, "scope_id": scope_id})

    if role_scope_values:
        role_scope_stmt = pg_insert(RoleScope).values(role_scope_values)
        role_scope_stmt = role_scope_stmt.on_conflict_do_nothing(
            index_elements=["role_id", "scope_id"]
        )
        await session.execute(role_scope_stmt)
    await session.commit()

    total_created = sum(results.values())
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
