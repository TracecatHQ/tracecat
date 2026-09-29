"""pin embedding recipe revision

Revision ID: 391f391da70b
Revises: 9680c861644a
Create Date: 2026-09-17 12:30:53.819826

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "391f391da70b"
down_revision: str | None = "9680c861644a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep NULL for legacy writers; new code rebuilds unpinned configurations.
    op.add_column(
        "search_embedding_config",
        sa.Column("recipe_revision", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_embedding_config", "recipe_revision")
