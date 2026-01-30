"""add_icon_name_to_case_dropdown_definition

Revision ID: 5f94298e9430
Revises: 328d927c631b
Create Date: 2026-01-30 11:18:53.104713

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f94298e9430"
down_revision: str | None = "328d927c631b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_dropdown_definition",
        sa.Column("icon_name", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("case_dropdown_definition", "icon_name")
