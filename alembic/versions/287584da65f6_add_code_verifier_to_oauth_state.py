"""add_code_verifier_to_oauth_state

Revision ID: 287584da65f6
Revises: a81bc08c39a1
Create Date: 2025-11-30 21:52:21.611614

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "287584da65f6"
down_revision: str | None = "a81bc08c39a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add code_verifier column for PKCE support in OAuth flows
    op.add_column("oauth_state", sa.Column("code_verifier", sa.Text(), nullable=True))


def downgrade() -> None:
    # Remove code_verifier column
    op.drop_column("oauth_state", "code_verifier")
