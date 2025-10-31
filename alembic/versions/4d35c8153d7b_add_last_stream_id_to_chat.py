"""add last_stream_id to chat

Revision ID: 4d35c8153d7b
Revises: 93f034d69301
Create Date: 2025-09-22 13:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d35c8153d7b"
down_revision: str | None = "93f034d69301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat",
        sa.Column("last_stream_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat", "last_stream_id")
