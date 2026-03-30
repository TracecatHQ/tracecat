"""add required_on_closure to dropdown definition

Revision ID: bf38f2aa1c77
Revises: 3431033d29fd
Create Date: 2026-03-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf38f2aa1c77"
down_revision: str | None = "3431033d29fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_dropdown_definition",
        sa.Column(
            "required_on_closure",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("case_dropdown_definition", "required_on_closure")
