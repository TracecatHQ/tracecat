"""add_tag_scope_column

Revision ID: e1d037cfa82a
Revises: d8f3e9a1b2c4
Create Date: 2025-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1d037cfa82a"
down_revision: str | None = "d8f3e9a1b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCOPE_DEFAULT = "both"


def upgrade() -> None:
    """Add scope column to tag with transitional defaults and supporting indexes."""

    op.add_column(
        "tag",
        sa.Column(
            "scope",
            sa.String(),
            nullable=False,
            server_default=sa.text(f"'{SCOPE_DEFAULT}'"),
        ),
    )
    op.create_index("ix_tag_owner_scope", "tag", ["owner_id", "scope"], unique=False)
    op.create_unique_constraint(
        "uq_tag_owner_scope_ref", "tag", ["owner_id", "scope", "ref"]
    )
    op.create_unique_constraint(
        "uq_tag_owner_scope_name", "tag", ["owner_id", "scope", "name"]
    )



def downgrade() -> None:
    """Remove scope column and related constraints from tag."""

    op.drop_constraint("uq_tag_owner_scope_name", "tag", type_="unique")
    op.drop_constraint("uq_tag_owner_scope_ref", "tag", type_="unique")
    op.drop_index("ix_tag_owner_scope", table_name="tag")
    op.drop_column("tag", "scope")
