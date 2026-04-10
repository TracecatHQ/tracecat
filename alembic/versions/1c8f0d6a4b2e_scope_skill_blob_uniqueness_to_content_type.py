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
