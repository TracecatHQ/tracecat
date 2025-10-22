"""merge migration heads

Revision ID: 355b91f2d698
Revises: 1f6f60502c17, d4fd132ccb50
Create Date: 2025-10-22 11:47:08.705701

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "355b91f2d698"
down_revision: str | None = ("1f6f60502c17", "d4fd132ccb50")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
