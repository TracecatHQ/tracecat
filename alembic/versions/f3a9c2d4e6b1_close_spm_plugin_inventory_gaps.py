"""close spm plugin inventory gaps

Revision ID: f3a9c2d4e6b1
Revises: c1f3b6a7d8e9
Create Date: 2026-04-30 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c2d4e6b1"
down_revision: str | None = "c1f3b6a7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                surrogate_id,
                relationship_type,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        organization_id,
                        endpoint_id,
                        from_inventory_item_id,
                        to_inventory_item_id
                    ORDER BY
                        CASE relationship_type
                            WHEN 'defines' THEN 0
                            WHEN 'contains' THEN 1
                            WHEN 'imports' THEN 2
                            ELSE 3
                        END,
                        updated_at DESC,
                        surrogate_id DESC
                ) AS row_number
            FROM spm_inventory_relationship
            WHERE relationship_type IN ('defines', 'contains', 'imports')
        )
        DELETE FROM spm_inventory_relationship AS relationship
        USING ranked
        WHERE relationship.surrogate_id = ranked.surrogate_id
          AND ranked.relationship_type IN ('contains', 'imports')
          AND ranked.row_number > 1
        """
    )
    op.execute(
        """
        UPDATE spm_inventory_relationship
        SET relationship_type = 'defines'
        WHERE relationship_type IN ('contains', 'imports')
        """
    )
    op.execute(
        """
        UPDATE spm_inventory_item
        SET item_type = 'subagent'
        WHERE item_type = 'agent'
        """
    )
    op.execute(
        """
        UPDATE spm_inventory_item
        SET source_type = 'subagent_frontmatter'
        WHERE source_type = 'agent_frontmatter'
        """
    )
    op.execute(
        """
        UPDATE spm_finding
        SET item_type = 'subagent'
        WHERE item_type = 'agent'
        """
    )
    op.execute(
        """
        UPDATE spm_finding
        SET source_type = 'subagent_frontmatter'
        WHERE source_type = 'agent_frontmatter'
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade is not supported because the upgrade collapses previous "
        "contains/imports relationship semantics into defines. Restore the "
        "database from backup or snapshot before rolling back the application."
    )
