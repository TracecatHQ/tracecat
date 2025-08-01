"""add_environment_to_action

Revision ID: 49d0fbd1431f
Revises: 419454d1c5c5
Create Date: 2025-07-21 13:29:27.479760

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49d0fbd1431f"
down_revision: str | None = "419454d1c5c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "action",
        sa.Column("environment", sa.String(), nullable=True),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("action", "environment")
    # ### end Alembic commands ###
