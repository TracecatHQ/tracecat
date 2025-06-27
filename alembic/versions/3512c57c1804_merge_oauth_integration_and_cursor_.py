"""merge oauth integration and cursor pagination

Revision ID: 3512c57c1804
Revises: d1693ed72940, 9c50a7f1ba96
Create Date: 2025-06-26 18:32:07.697141

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3512c57c1804"
down_revision: tuple[str, str] = ("d1693ed72940", "9c50a7f1ba96")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
