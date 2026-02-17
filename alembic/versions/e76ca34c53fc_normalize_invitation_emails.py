"""normalize invitation emails

Revision ID: e76ca34c53fc
Revises: 2e1f96db2255
Create Date: 2026-02-17 14:01:03.459864

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e76ca34c53fc"
down_revision: str | None = "2e1f96db2255"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Normalize existing invitation emails to lowercase
    op.execute("UPDATE invitation SET email = lower(email) WHERE email != lower(email)")
    op.execute(
        "UPDATE organization_invitation SET email = lower(email) WHERE email != lower(email)"
    )


def downgrade() -> None:
    # Cannot restore original casing
    pass
