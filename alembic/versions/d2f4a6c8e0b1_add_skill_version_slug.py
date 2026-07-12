"""add skill version slug

Revision ID: d2f4a6c8e0b1
Revises: 44320bf05445
Create Date: 2026-07-12 00:00:00.000000

The column remains nullable during the expand window because old application
pods still publish versions without it. New writers dual-write ``name`` and
``slug``; readers fall back to ``name`` until the contract release. Existing
head slugs may have collision suffixes from the earlier migration, so this
revision does not rewrite Skill heads or immutable package contents.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f4a6c8e0b1"
down_revision: str | None = "44320bf05445"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skill_version",
        sa.Column("slug", sa.String(length=64), nullable=True),
    )
    op.execute("UPDATE skill_version SET slug = name WHERE slug IS NULL")


def downgrade() -> None:
    op.drop_column("skill_version", "slug")
