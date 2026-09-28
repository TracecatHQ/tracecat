"""add mcp catalog slug and oauth token endpoint auth method

Revision ID: 9b52f7f18a31
Revises: c9d2e7a4f1b3
Create Date: 2026-06-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b52f7f18a31"
down_revision: str | None = "c9d2e7a4f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_integration",
        sa.Column("catalog_slug", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_mcp_integration_workspace_catalog_slug",
        "mcp_integration",
        ["workspace_id", "catalog_slug"],
        unique=False,
    )
    op.add_column(
        "oauth_integration",
        sa.Column("token_endpoint_auth_method", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_integration", "token_endpoint_auth_method")
    op.drop_index(
        "ix_mcp_integration_workspace_catalog_slug",
        table_name="mcp_integration",
    )
    op.drop_column("mcp_integration", "catalog_slug")
