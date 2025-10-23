"""empty message

Revision ID: 799af90b73bf
Revises: 355b91f2d698, af3134e6907d
Create Date: 2025-10-23 11:43:49.359302

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "799af90b73bf"
down_revision: Sequence[str] | str | None = ("355b91f2d698", "af3134e6907d")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
