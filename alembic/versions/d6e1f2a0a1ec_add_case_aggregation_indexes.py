"""add case aggregation indexes

Revision ID: d6e1f2a0a1ec
Revises: 8f4f1bd13e9c
Create Date: 2026-03-02 16:35:53.844817

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e1f2a0a1ec"
down_revision: str | None = "8f4f1bd13e9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_case_tag_link_tag_case",
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
    op.drop_index("ix_case_tag_link_tag_case", table_name="case_tag_link")
