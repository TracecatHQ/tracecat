"""migrate membership roles to rbac assignments

Revision ID: 97812277aa15
Revises: 60a5af5effdd
Create Date: 2026-02-13 12:34:28.832810

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "97812277aa15"
down_revision: str | None = "60a5af5effdd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill org-level role assignments from organization_membership.
    # Keep legacy membership role columns in place for now.
    op.execute(
        """
        INSERT INTO user_role_assignment (
            id,
            organization_id,
            user_id,
            workspace_id,
            role_id,
            assigned_at,
            assigned_by
        )
        SELECT
            gen_random_uuid(),
            om.organization_id,
            om.user_id,
            NULL::uuid,
            r.id,
            NOW(),
            NULL::uuid
        FROM organization_membership AS om
        JOIN role AS r
          ON r.organization_id = om.organization_id
         AND r.slug = CASE om.role::text
             WHEN 'OWNER' THEN 'organization-owner'
             WHEN 'ADMIN' THEN 'organization-admin'
             WHEN 'MEMBER' THEN 'organization-member'
         END
        ON CONFLICT (user_id)
        WHERE workspace_id IS NULL
        DO UPDATE
        SET
            organization_id = EXCLUDED.organization_id,
            role_id = EXCLUDED.role_id
        """
    )

    # Backfill workspace-level role assignments from membership.
    op.execute(
        """
        INSERT INTO user_role_assignment (
            id,
            organization_id,
            user_id,
            workspace_id,
            role_id,
            assigned_at,
            assigned_by
        )
        SELECT
            gen_random_uuid(),
            w.organization_id,
            m.user_id,
            m.workspace_id,
            r.id,
            NOW(),
            NULL::uuid
        FROM membership AS m
        JOIN workspace AS w
          ON w.id = m.workspace_id
        JOIN role AS r
          ON r.organization_id = w.organization_id
         AND r.slug = CASE m.role::text
             WHEN 'ADMIN' THEN 'workspace-admin'
             WHEN 'EDITOR' THEN 'workspace-editor'
             WHEN 'VIEWER' THEN 'workspace-viewer'
         END
        ON CONFLICT (user_id, workspace_id)
        DO UPDATE
        SET
            organization_id = EXCLUDED.organization_id,
            role_id = EXCLUDED.role_id
        """
    )


def downgrade() -> None:
    # Data migration is intentionally not reversed.
    return None
