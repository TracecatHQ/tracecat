"""Add case search aggregation indexes.

Revision ID: 9f2d8c73b6a1
Revises: 8f4f1bd13e9c
Create Date: 2026-03-02 23:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f2d8c73b6a1"
down_revision: str | None = "8f4f1bd13e9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_case_tag_link_tag_id_case_id",
        "case_tag_link",
        ["tag_id", "case_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_dropdown_value_definition_option_case",
        "case_dropdown_value",
        ["definition_id", "option_id", "case_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_case_dropdown_value_definition_option_case",
        table_name="case_dropdown_value",
    )
    op.drop_index("ix_case_tag_link_tag_id_case_id", table_name="case_tag_link")
