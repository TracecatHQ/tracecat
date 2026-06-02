"""add mcp_integrations to agent_session

Revision ID: 0f18bea0c115
Revises: b7f0d1e2c3a4
Create Date: 2026-05-31 16:40:58.021732

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f18bea0c115"
down_revision: str | None = "b7f0d1e2c3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column(
            "mcp_integrations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "mcp_integrations")
