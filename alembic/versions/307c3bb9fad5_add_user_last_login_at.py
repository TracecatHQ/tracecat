"""Add user last_login_at

Revision ID: 307c3bb9fad5
Revises: 0a85ea95e68c
Create Date: 2024-12-04 10:43:38.280178

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "307c3bb9fad5"
down_revision: str | None = "0a85ea95e68c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "user", sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("user", "last_login_at")
    # ### end Alembic commands ###
