"""empty message

Revision ID: e5eebe57a7b8
Revises: 3da6f9f95dda, 70144f614d3d
Create Date: 2025-11-10 13:49:56.682737

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e5eebe57a7b8"
down_revision: str | None = ("3da6f9f95dda", "70144f614d3d")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
