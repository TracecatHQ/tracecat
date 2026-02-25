"""Canonicalize duplicate custom action scopes to platform scopes.

For deployments where registry action scopes were seeded as both PLATFORM
(organization_id=NULL) and CUSTOM (organization_id=<org_id>) with the same
name, remap role_scope links to the canonical platform scope, remove stale
custom links, and delete the redundant custom scope rows.

Revision ID: c9e4f54f0a2b
Revises: 672fecea1d32
Create Date: 2026-02-25 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4f54f0a2b"
down_revision: str | None = "672fecea1d32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: Remap role_scope links from duplicate custom scopes to the
    # canonical platform scope with the same name.
    op.execute(
        """
        WITH duplicate_scope_pairs AS (
            SELECT custom_scope.id AS custom_scope_id, platform_scope.id AS platform_scope_id
            FROM scope AS custom_scope
            JOIN scope AS platform_scope
                ON platform_scope.name = custom_scope.name
               AND platform_scope.source_ref = custom_scope.source_ref
            WHERE custom_scope.organization_id IS NOT NULL
              AND custom_scope.source = 'CUSTOM'
              AND custom_scope.resource = 'action'
              AND custom_scope.action = 'execute'
              AND custom_scope.source_ref IS NOT NULL
              AND platform_scope.organization_id IS NULL
              AND platform_scope.source = 'PLATFORM'
              AND platform_scope.resource = 'action'
              AND platform_scope.action = 'execute'
        )
        INSERT INTO role_scope (role_id, scope_id)
        SELECT role_scope.role_id, duplicate_scope_pairs.platform_scope_id
        FROM role_scope
        JOIN duplicate_scope_pairs
            ON duplicate_scope_pairs.custom_scope_id = role_scope.scope_id
        ON CONFLICT (role_id, scope_id) DO NOTHING
        """
    )

    # Step 2: Drop stale role_scope links that point to custom duplicates.
    op.execute(
        """
        WITH duplicate_scope_pairs AS (
            SELECT custom_scope.id AS custom_scope_id
            FROM scope AS custom_scope
            JOIN scope AS platform_scope
                ON platform_scope.name = custom_scope.name
               AND platform_scope.source_ref = custom_scope.source_ref
            WHERE custom_scope.organization_id IS NOT NULL
              AND custom_scope.source = 'CUSTOM'
              AND custom_scope.resource = 'action'
              AND custom_scope.action = 'execute'
              AND custom_scope.source_ref IS NOT NULL
              AND platform_scope.organization_id IS NULL
              AND platform_scope.source = 'PLATFORM'
              AND platform_scope.resource = 'action'
              AND platform_scope.action = 'execute'
        )
        DELETE FROM role_scope
        USING duplicate_scope_pairs
        WHERE role_scope.scope_id = duplicate_scope_pairs.custom_scope_id
        """
    )

    # Step 3: Delete the now-orphaned custom scope rows that are duplicates
    # of platform scopes. After steps 1-2, no role_scope links reference
    # these rows, so deletion is safe.
    op.execute(
        """
        DELETE FROM scope
        USING scope AS platform_scope
        WHERE scope.organization_id IS NOT NULL
          AND scope.source = 'CUSTOM'
          AND scope.resource = 'action'
          AND scope.action = 'execute'
          AND scope.source_ref IS NOT NULL
          AND platform_scope.organization_id IS NULL
          AND platform_scope.source = 'PLATFORM'
          AND platform_scope.resource = 'action'
          AND platform_scope.action = 'execute'
          AND platform_scope.source_ref = scope.source_ref
          AND platform_scope.name = scope.name
        """
    )


def downgrade() -> None:
    # Irreversible data migration: removed duplicate custom scope rows
    # and their role_scope links cannot be reconstructed safely.
    pass
