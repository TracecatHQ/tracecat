"""drop provider_config column from oauth_integration

Revision ID: b4a6dcdf1012
Revises: 67914e68c877
Create Date: 2024-09-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b4a6dcdf1012"
down_revision: str | None = "67914e68c877"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oauth_integration",
        sa.Column("authorization_endpoint", sa.Text(), nullable=True),
    )
    op.add_column(
        "oauth_integration",
        sa.Column("token_endpoint", sa.Text(), nullable=True),
    )

    connection = op.get_bind()
    # Clear existing integrations so that every workspace must reconfigure with
    # explicit endpoints. This avoids attempting to translate legacy configs.
    connection.execute(sa.text("DELETE FROM oauth_integration"))

    op.drop_column("oauth_integration", "provider_config")


def downgrade() -> None:
    op.add_column(
        "oauth_integration",
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE oauth_integration
            SET provider_config = jsonb_strip_nulls(
                jsonb_build_object(
                    'authorization_endpoint', authorization_endpoint,
                    'token_endpoint', token_endpoint
                )
            )
            """
        )
    )
    op.alter_column(
        "oauth_integration",
        "provider_config",
        server_default=None,
    )

    op.drop_column("oauth_integration", "token_endpoint")
    op.drop_column("oauth_integration", "authorization_endpoint")
