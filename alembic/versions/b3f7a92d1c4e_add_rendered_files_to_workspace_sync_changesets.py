"""add rendered files to workspace sync changesets

Revision ID: b3f7a92d1c4e
Revises: 25f4e2a1c9d8
Create Date: 2026-06-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7a92d1c4e"
down_revision: str | None = "25f4e2a1c9d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_sync_changeset",
        sa.Column(
            "rendered_files",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_sync_changeset", "rendered_files")
