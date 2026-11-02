"""Merge the skill folders and search selection heads.

Revision ID: 2f14222e0d12
Revises: 8c0e18190001, acbacbf8ef53
Create Date: 2026-09-25 11:53:35.054859

Both parents are additive and independent. This merge exists only so that
``alembic upgrade head`` resolves to a single revision again; it applies no
schema changes.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "2f14222e0d12"
down_revision: tuple[str, str] | None = ("8c0e18190001", "acbacbf8ef53")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
