"""add registry_lock to workflow_definition

Revision ID: 74a426bea776
Revises: 8b00d38127c5
Create Date: 2025-12-10 13:22:36.701920

This migration:
1. Adds registry_lock column to workflow_definition table
2. Backfills registry_lock on workflow and workflow_definition tables
   using the latest RegistryVersion for each RegistryRepository
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "74a426bea776"
down_revision: str | None = "91cb48845f36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add registry_lock column to workflow_definition
    op.add_column(
        "workflow_definition",
        sa.Column(
            "registry_lock", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    # 2. Backfill registry_lock on workflow and workflow_definition
    # Get the latest RegistryVersion for each RegistryRepository and build the lock
    conn = op.get_bind()

    # Query to get latest version per repository
    # Uses DISTINCT ON to get the most recent version for each repository
    latest_versions_query = sa.text("""
        SELECT rr.origin, rv.version
        FROM registry_version rv
        JOIN registry_repository rr ON rv.repository_id = rr.id
        WHERE rv.created_at = (
            SELECT MAX(rv2.created_at)
            FROM registry_version rv2
            WHERE rv2.repository_id = rv.repository_id
        )
    """)

    result = conn.execute(latest_versions_query)
    rows = result.fetchall()

    if rows:
        # Build the lock dict: {origin: version}
        registry_lock: dict[str, str] = dict(rows)  # type: ignore[arg-type]
        lock_json = json.dumps(registry_lock)
        print(f"Backfilling registry_lock with: {registry_lock}")

        # Update all workflows that don't have a registry_lock
        conn.execute(
            sa.text("""
                UPDATE workflow
                SET registry_lock = :lock::jsonb
                WHERE registry_lock IS NULL
            """),
            {"lock": lock_json},
        )

        # Update all workflow_definitions that don't have a registry_lock
        conn.execute(
            sa.text("""
                UPDATE workflow_definition
                SET registry_lock = :lock::jsonb
                WHERE registry_lock IS NULL
            """),
            {"lock": lock_json},
        )

        print("Backfill complete")
    else:
        print("No RegistryVersions found, skipping backfill")


def downgrade() -> None:
    op.drop_column("workflow_definition", "registry_lock")
