"""backfill secret namespaces into integrations

Revision ID: c8f2d1e4a5b6
Revises: b7e1c9f2a3d4
Create Date: 2026-05-25 23:00:00.000000

For secret names that don't already have an Integration row, a workspace-scoped
``Integration`` is created on demand so the Integrations catalog can project
legacy static credentials without moving credential storage.

Additive only -- the legacy ``secret`` table remains the source of truth for
static/API-key credentials.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f2d1e4a5b6"
down_revision: str | None = "b7e1c9f2a3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill workspace-scoped Integration rows for every Secret name that
    # doesn't already have a catalog entry. Source=workspace so we can tell
    # these apart from the platform-seeded providers.
    op.execute(
        """
        INSERT INTO integration (
            id, workspace_id, namespace, display_name, description,
            source, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            s.workspace_id,
            s.name,
            INITCAP(REPLACE(s.name, '_', ' ')),
            'Backfilled from legacy credential.',
            'workspace',
            NOW(),
            NOW()
        FROM (
            SELECT DISTINCT workspace_id, name
            FROM secret
        ) AS s
        WHERE NOT EXISTS (
            SELECT 1
            FROM integration i
            WHERE i.namespace = s.name
              AND (
                i.workspace_id IS NULL
                OR i.workspace_id = s.workspace_id
              )
        )
        """
    )


def downgrade() -> None:
    # This migration only adds catalog rows for pre-existing secret names.
    # Leave them in place on downgrade; deleting them could hide credentials
    # from the catalog after a downgrade/upgrade loop.
    pass
