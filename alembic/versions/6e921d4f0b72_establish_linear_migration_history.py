"""Establish linear migration history without changing schema or data.

Revision ID: 6e921d4f0b72
Revises: 8c0e18190001, acbacbf8ef53
Create Date: 2026-09-25 11:55:11.920832

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6e921d4f0b72"
down_revision: tuple[str, str] = ("8c0e18190001", "acbacbf8ef53")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
