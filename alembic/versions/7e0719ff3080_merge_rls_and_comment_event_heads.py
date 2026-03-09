"""merge rls and comment event heads

Revision ID: 7e0719ff3080
Revises: 3b58a1430e95, c76f9b01fad7
Create Date: 2026-03-08 21:08:13.101247

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "7e0719ff3080"
down_revision: tuple[str, str] = ("3b58a1430e95", "c76f9b01fad7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
