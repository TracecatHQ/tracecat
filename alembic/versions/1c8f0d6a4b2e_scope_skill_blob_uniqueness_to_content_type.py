"""scope skill blob uniqueness to content type

Revision ID: 1c8f0d6a4b2e
Revises: 0c6bb8f8e1d1
Create Date: 2026-04-10 02:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c8f0d6a4b2e"
down_revision: str | None = "0c6bb8f8e1d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _reconcile_skill_blob_duplicates_for_downgrade() -> None:
    """Collapse content-type-split blobs back to one row per workspace and digest."""

    duplicate_blob_cte = """
    WITH duplicate_blobs AS (
        SELECT
            id AS duplicate_id,
            MIN(id) OVER (PARTITION BY workspace_id, sha256) AS canonical_id
        FROM skill_blob
    )
    """

    op.execute(
        duplicate_blob_cte
        + """
        UPDATE skill_draft_file AS draft_file
        SET blob_id = duplicate_blobs.canonical_id
        FROM duplicate_blobs
        WHERE draft_file.blob_id = duplicate_blobs.duplicate_id
          AND duplicate_blobs.duplicate_id <> duplicate_blobs.canonical_id
        """
    )
    op.execute(
        duplicate_blob_cte
        + """
        UPDATE skill_version_file AS version_file
        SET blob_id = duplicate_blobs.canonical_id
        FROM duplicate_blobs
        WHERE version_file.blob_id = duplicate_blobs.duplicate_id
          AND duplicate_blobs.duplicate_id <> duplicate_blobs.canonical_id
        """
    )
    op.execute(
        duplicate_blob_cte
        + """
        UPDATE skill_upload AS upload
        SET blob_id = duplicate_blobs.canonical_id
        FROM duplicate_blobs
        WHERE upload.blob_id = duplicate_blobs.duplicate_id
          AND duplicate_blobs.duplicate_id <> duplicate_blobs.canonical_id
        """
    )
    op.execute(
        duplicate_blob_cte
        + """
        DELETE FROM skill_blob
        USING duplicate_blobs
        WHERE skill_blob.id = duplicate_blobs.duplicate_id
          AND duplicate_blobs.duplicate_id <> duplicate_blobs.canonical_id
        """
    )


def upgrade() -> None:
    op.drop_constraint(
        "uq_skill_blob_workspace_sha256",
        "skill_blob",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_skill_blob_workspace_sha256_content_type",
        "skill_blob",
        ["workspace_id", "sha256", "content_type"],
    )


def downgrade() -> None:
    _reconcile_skill_blob_duplicates_for_downgrade()
    op.drop_constraint(
        "uq_skill_blob_workspace_sha256_content_type",
        "skill_blob",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_skill_blob_workspace_sha256",
        "skill_blob",
        ["workspace_id", "sha256"],
    )
