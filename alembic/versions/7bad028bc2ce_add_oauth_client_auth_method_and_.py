"""add oauth client auth method and assertion fields

Revision ID: 7bad028bc2ce
Revises: 8f4f1bd13e9c
Create Date: 2026-03-02 12:42:01.111005

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7bad028bc2ce"
down_revision: str | None = "8f4f1bd13e9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    oauth_client_auth_method = sa.Enum(
        "AUTO",
        "CLIENT_SECRET_BASIC",
        "CLIENT_SECRET_POST",
        "PRIVATE_KEY_JWT",
        "NONE",
        name="oauthclientauthmethod",
    )
    oauth_client_auth_method.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "oauth_integration",
        sa.Column(
            "client_auth_method",
            postgresql.ENUM(
                "AUTO",
                "CLIENT_SECRET_BASIC",
                "CLIENT_SECRET_POST",
                "PRIVATE_KEY_JWT",
                "NONE",
                name="oauthclientauthmethod",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "oauth_integration",
        sa.Column(
            "encrypted_client_assertion_private_key", sa.LargeBinary(), nullable=True
        ),
    )
    op.add_column(
        "oauth_integration",
        sa.Column(
            "encrypted_client_assertion_certificate", sa.LargeBinary(), nullable=True
        ),
    )
    op.add_column(
        "oauth_integration",
        sa.Column("client_assertion_kid", sa.String(), nullable=True),
    )
    op.add_column(
        "oauth_integration",
        sa.Column("client_assertion_alg", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_integration", "client_assertion_alg")
    op.drop_column("oauth_integration", "client_assertion_kid")
    op.drop_column("oauth_integration", "encrypted_client_assertion_certificate")
    op.drop_column("oauth_integration", "encrypted_client_assertion_private_key")
    op.drop_column("oauth_integration", "client_auth_method")

    oauth_client_auth_method = sa.Enum(
        "AUTO",
        "CLIENT_SECRET_BASIC",
        "CLIENT_SECRET_POST",
        "PRIVATE_KEY_JWT",
        "NONE",
        name="oauthclientauthmethod",
    )
    oauth_client_auth_method.drop(op.get_bind(), checkfirst=True)
