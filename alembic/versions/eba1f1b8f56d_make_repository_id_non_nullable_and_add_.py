"""Make repository_id non-nullable and add cascade delete

Revision ID: eba1f1b8f56d
Revises: 36f47d3628bf
Create Date: 2024-10-10 15:22:54.935271

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eba1f1b8f56d"
down_revision: str | None = "36f47d3628bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "registryaction", "repository_id", existing_type=sa.UUID(), nullable=True
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "registryaction", "repository_id", existing_type=sa.UUID(), nullable=False
    )
    # ### end Alembic commands ###
