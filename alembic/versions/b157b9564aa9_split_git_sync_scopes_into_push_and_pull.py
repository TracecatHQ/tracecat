"""Split Git sync scopes into push and pull.

Adds `workspace_sync:push` and `workspace_sync:pull`, which replace
`workflow:sync` and `workspace_sync:sync` (each granted both directions).
Roles and service accounts holding an old scope, directly or through a
wildcard, get both new scopes so no one loses access. `workspace-editor`
only gets push; startup seeding reconciles the other preset roles.

The old scope rows and their links stay so the previous app version keeps
working during rollout. Drop them in a later contract migration.

Revision ID: b157b9564aa9
Revises: 002febfa86c3
Create Date: 2026-10-28 19:16:24.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b157b9564aa9"
down_revision: str | None = "002febfa86c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_SCOPES = "('workspace_sync:push', 'workspace_sync:pull')"

# A granted scope covers an old sync scope when its fnmatch-style pattern
# matches it, mirroring `tracecat.authz.controls.scope_matches`.
GRANTS_OLD_SCOPE = """
    EXISTS (
        SELECT 1
        FROM unnest(ARRAY['workflow:sync', 'workspace_sync:sync']) AS old(name)
        WHERE old.name LIKE replace(
            replace(replace(granted.name, '%', '\\%'), '_', '\\_'), '*', '%'
        )
    )
"""


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO scope (
            id, name, resource, action, description, source, source_ref,
            organization_id
        )
        VALUES
            (
                gen_random_uuid(), 'workspace_sync:push', 'workspace_sync', 'push',
                'Push workspace resources to the configured Git repository',
                'PLATFORM', NULL, NULL
            ),
            (
                gen_random_uuid(), 'workspace_sync:pull', 'workspace_sync', 'pull',
                'Pull workspace resources from the configured Git repository',
                'PLATFORM', NULL, NULL
            )
        ON CONFLICT (name) WHERE organization_id IS NULL DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO role_scope (role_id, scope_id)
        SELECT DISTINCT role.id, new_scope.id
        FROM role_scope
        JOIN role ON role.id = role_scope.role_id
        JOIN scope AS granted ON granted.id = role_scope.scope_id
        CROSS JOIN scope AS new_scope
        WHERE {GRANTS_OLD_SCOPE}
          AND new_scope.organization_id IS NULL
          AND new_scope.name IN {NEW_SCOPES}
          AND NOT (
              role.slug IS NOT DISTINCT FROM 'workspace-editor'
              AND new_scope.name = 'workspace_sync:pull'
          )
        ON CONFLICT (role_id, scope_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO service_account_scope (service_account_id, scope_id)
        SELECT DISTINCT sa_scope.service_account_id, new_scope.id
        FROM service_account_scope AS sa_scope
        JOIN scope AS granted ON granted.id = sa_scope.scope_id
        CROSS JOIN scope AS new_scope
        WHERE {GRANTS_OLD_SCOPE}
          AND new_scope.organization_id IS NULL
          AND new_scope.name IN {NEW_SCOPES}
        ON CONFLICT (service_account_id, scope_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE scope
        SET description = 'Deprecated: use workspace_sync:push and workspace_sync:pull'
        WHERE organization_id IS NULL
          AND name IN ('workflow:sync', 'workspace_sync:sync')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE scope
        SET description = CASE name
            WHEN 'workflow:sync'
                THEN 'Sync workflows with the configured Git repository'
            ELSE 'Sync workspace resources with the configured Git repository'
        END
        WHERE organization_id IS NULL
          AND name IN ('workflow:sync', 'workspace_sync:sync')
        """
    )
    # Cascades to role_scope and service_account_scope links.
    op.execute(
        f"""
        DELETE FROM scope
        WHERE organization_id IS NULL AND name IN {NEW_SCOPES}
        """
    )
