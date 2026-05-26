"""add_agent_session_status

Revision ID: e1a2b3c4d5f6
Revises: a3d7c9e8b4f2
Create Date: 2026-05-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5f6"
down_revision: str | None = "a3d7c9e8b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="idle",
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_agent_session_status"),
        "agent_session",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_session_status"), table_name="agent_session")
    op.drop_column("agent_session", "status")
