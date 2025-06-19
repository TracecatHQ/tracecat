"""add_client_credentials_to_oauth_integration

Revision ID: a1718b08194f
Revises: f1654d579c16
Create Date: 2025-06-19 01:22:22.681099

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1718b08194f"
down_revision: str | None = "f1654d579c16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add encrypted client credentials fields to oauth_integration table
    op.add_column(
        "oauth_integration",
        sa.Column("encrypted_client_id", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "oauth_integration",
        sa.Column("encrypted_client_secret", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "oauth_integration",
        sa.Column(
            "use_workspace_credentials",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    # Remove the client credentials fields
    op.drop_column("oauth_integration", "use_workspace_credentials")
    op.drop_column("oauth_integration", "encrypted_client_secret")
    op.drop_column("oauth_integration", "encrypted_client_id")
