"""merge spm and service account heads

Revision ID: 7f3d8721246a
Revises: ed7b7d97ede5, 548aa7691799
Create Date: 2026-04-23 09:34:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "7f3d8721246a"
down_revision: tuple[str, str] | None = ("ed7b7d97ede5", "548aa7691799")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
