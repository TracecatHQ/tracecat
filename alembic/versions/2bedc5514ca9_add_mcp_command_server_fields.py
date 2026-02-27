"""add_mcp_stdio_server_fields

Revision ID: 2bedc5514ca9
Revises: c9e4f54f0a2b
Create Date: 2026-02-02 14:42:10.977157

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2bedc5514ca9"
down_revision: str | None = "c9e4f54f0a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_integration",
        sa.Column(
            "server_type", sa.String(length=20), server_default="http", nullable=False
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column("stdio_command", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "mcp_integration",
        sa.Column("stdio_args", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "mcp_integration",
        sa.Column("encrypted_stdio_env", sa.LargeBinary(), nullable=True),
    )
    op.add_column("mcp_integration", sa.Column("timeout", sa.Integer(), nullable=True))
    op.alter_column(
        "mcp_integration", "server_uri", existing_type=sa.VARCHAR(), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "mcp_integration", "server_uri", existing_type=sa.VARCHAR(), nullable=False
    )
    op.drop_column("mcp_integration", "timeout")
    op.drop_column("mcp_integration", "encrypted_stdio_env")
    op.drop_column("mcp_integration", "stdio_args")
    op.drop_column("mcp_integration", "stdio_command")
    op.drop_column("mcp_integration", "server_type")
