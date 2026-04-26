"""merge subagent and case anchor migration heads

Revision ID: 4f2b0e1d3c9a
Revises: 9f0b5f6a2d1c, aa5951a3373f
Create Date: 2026-04-26 18:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4f2b0e1d3c9a"
down_revision: tuple[str, str] | None = ("9f0b5f6a2d1c", "aa5951a3373f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
