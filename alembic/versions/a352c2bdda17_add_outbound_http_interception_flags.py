"""add outbound HTTP interception flags

Revision ID: a352c2bdda17
Revises: 6171727be56a
Create Date: 2026-03-10 16:20:58.046254

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a352c2bdda17"
down_revision: str | None = "6171727be56a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column(
            "outbound_http_interception_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "outbound_http_interception_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow", "outbound_http_interception_enabled")
    op.drop_column("agent_session", "outbound_http_interception_enabled")
