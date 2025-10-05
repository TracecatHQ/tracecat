"""backfill tag scopes

Revision ID: 5f4ce2d1dc3a
Revises: e1d037cfa82a
Create Date: 2025-10-03 00:10:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f4ce2d1dc3a"
down_revision: str | None = "e1d037cfa82a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WORKFLOW_SCOPE = "workflow"
CASE_SCOPE = "case"


def _drop_legacy_uniques() -> None:
    """Drop legacy uniqueness constraints that ignore scope."""

    op.execute("ALTER TABLE tag DROP CONSTRAINT IF EXISTS uq_tag_ref_owner")
    op.execute("ALTER TABLE tag DROP CONSTRAINT IF EXISTS tag_name_owner_id_key")


def _update_single_scope_tags(conn: sa.Connection) -> None:
    """Assign scope for tags used exclusively by workflows or cases."""

    # Tags linked only to workflows → workflow scope
    conn.execute(
        sa.text(
            """
            WITH workflow_only AS (
                SELECT t.id
                FROM tag AS t
                WHERE EXISTS (
                    SELECT 1 FROM workflowtag AS wt WHERE wt.tag_id = t.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM casetag AS ct WHERE ct.tag_id = t.id
                )
            )
            UPDATE tag
            SET scope = :workflow_scope
            WHERE id IN (SELECT id FROM workflow_only)
            """
        ),
        {"workflow_scope": WORKFLOW_SCOPE},
    )

    # Tags linked only to cases → case scope
    conn.execute(
        sa.text(
            """
            WITH case_only AS (
                SELECT t.id
                FROM tag AS t
                WHERE EXISTS (
                    SELECT 1 FROM casetag AS ct WHERE ct.tag_id = t.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM workflowtag AS wt WHERE wt.tag_id = t.id
                )
            )
            UPDATE tag
            SET scope = :case_scope
            WHERE id IN (SELECT id FROM case_only)
            """
        ),
        {"case_scope": CASE_SCOPE},
    )


def _split_shared_tags(conn: sa.Connection) -> None:
    """Duplicate shared tags so workflows and cases each reference a scoped tag."""

    shared_tags = conn.execute(
        sa.text(
            """
            SELECT id, owner_id, name, ref, color
            FROM tag
            WHERE EXISTS (
                SELECT 1 FROM workflowtag AS wt WHERE wt.tag_id = tag.id
            )
            AND EXISTS (
                SELECT 1 FROM casetag AS ct WHERE ct.tag_id = tag.id
            )
            """
        )
    ).mappings().all()

    for tag in shared_tags:
        new_tag_id = uuid.uuid4()

        # Insert workflow-scoped duplicate
        conn.execute(
            sa.text(
                """
                INSERT INTO tag (id, owner_id, name, ref, color, scope)
                VALUES (:id, :owner_id, :name, :ref, :color, :scope)
                """
            ),
            {
                "id": new_tag_id,
                "owner_id": tag["owner_id"],
                "name": tag["name"],
                "ref": tag["ref"],
                "color": tag["color"],
                "scope": WORKFLOW_SCOPE,
            },
        )

        # Repoint workflow associations to the new tag
        conn.execute(
            sa.text(
                """
                UPDATE workflowtag
                SET tag_id = :new_tag_id
                WHERE tag_id = :old_tag_id
                """
            ),
            {"new_tag_id": new_tag_id, "old_tag_id": tag["id"]},
        )

        # Ensure the original tag becomes case-scoped
        conn.execute(
            sa.text(
                """
                UPDATE tag
                SET scope = :case_scope
                WHERE id = :tag_id
                """
            ),
            {"case_scope": CASE_SCOPE, "tag_id": tag["id"]},
        )


def _default_remaining_tags(conn: sa.Connection) -> None:
    """Assign workflow scope to any remaining transitional entries."""

    conn.execute(
        sa.text(
            """
            UPDATE tag
            SET scope = :workflow_scope
            WHERE scope NOT IN (:workflow_scope, :case_scope)
            """
        ),
        {"workflow_scope": WORKFLOW_SCOPE, "case_scope": CASE_SCOPE},
    )



def upgrade() -> None:
    conn = op.get_bind()

    _drop_legacy_uniques()
    _update_single_scope_tags(conn)
    _split_shared_tags(conn)
    _default_remaining_tags(conn)



def downgrade() -> None:
    raise NotImplementedError("Downgrading tag scope backfill is not supported")
