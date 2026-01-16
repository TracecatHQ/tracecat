"""Seed default organization and enforce workspace org FK.

Revision ID: 4a298374b126
Revises: 7aab03def5b6, c2a4f8a5cf72
Create Date: 2026-01-16 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a298374b126"
down_revision: tuple[str, str] | None = ("7aab03def5b6", "c2a4f8a5cf72")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ensure a default organization row exists for the sentinel UUID.
    op.execute(
        """
        INSERT INTO organization (id, name, slug, is_active, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000000',
            'Default Organization',
            'default',
            true,
            now(),
            now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # Backfill organization rows for any pre-existing workspaces with non-default orgs.
    op.execute(
        """
        INSERT INTO organization (id, name, slug, is_active, created_at, updated_at)
        SELECT DISTINCT
            w.organization_id,
            'Organization ' || w.organization_id::text,
            'org-' || replace(w.organization_id::text, '-', ''),
            true,
            now(),
            now()
        FROM workspace AS w
        LEFT JOIN organization AS o ON o.id = w.organization_id
        WHERE o.id IS NULL
        ON CONFLICT (id) DO NOTHING
        """
    )

    op.create_index(
        op.f("ix_workspace_organization_id"),
        "workspace",
        ["organization_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_workspace_organization_id_organization"),
        "workspace",
        "organization",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_workspace_org_change()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'workspace.organization_id is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER workspace_organization_id_immutable
        BEFORE UPDATE OF organization_id ON workspace
        FOR EACH ROW
        EXECUTE FUNCTION prevent_workspace_org_change();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS workspace_organization_id_immutable ON workspace"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_workspace_org_change")
    op.drop_constraint(
        op.f("fk_workspace_organization_id_organization"),
        "workspace",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_workspace_organization_id"), table_name="workspace")
