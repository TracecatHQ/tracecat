"""add case dropdown tables

Revision ID: 328d927c631b
Revises: 49a5c7464ab7
Create Date: 2026-01-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "328d927c631b"
down_revision: str | None = "49a5c7464ab7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Case Dropdown Definition
    op.create_table(
        "case_dropdown_definition",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column(
            "is_ordered", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name="fk_case_dropdown_definition_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint(
            "ref", "workspace_id", name="uq_case_dropdown_definition_ref_workspace"
        ),
    )
    op.create_index(
        "ix_case_dropdown_definition_id",
        "case_dropdown_definition",
        ["id"],
    )
    op.create_index(
        "ix_case_dropdown_definition_ref",
        "case_dropdown_definition",
        ["ref"],
    )

    # Case Dropdown Option
    op.create_table(
        "case_dropdown_option",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("icon_name", sa.String(length=100), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["case_dropdown_definition.id"],
            name="fk_case_dropdown_option_definition",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint(
            "ref", "definition_id", name="uq_case_dropdown_option_ref_definition"
        ),
    )
    op.create_index(
        "ix_case_dropdown_option_id",
        "case_dropdown_option",
        ["id"],
    )
    op.create_index(
        "ix_case_dropdown_option_ref",
        "case_dropdown_option",
        ["ref"],
    )

    # Case Dropdown Value (per-case single-select)
    op.create_table(
        "case_dropdown_value",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("option_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name="fk_case_dropdown_value_case",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["case_dropdown_definition.id"],
            name="fk_case_dropdown_value_definition",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["case_dropdown_option.id"],
            name="fk_case_dropdown_value_option",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "definition_id",
            name="uq_case_dropdown_value_case_definition",
        ),
    )
    op.create_index(
        "ix_case_dropdown_value_id",
        "case_dropdown_value",
        ["id"],
    )


def downgrade() -> None:
    op.drop_table("case_dropdown_value")
    op.drop_table("case_dropdown_option")
    op.drop_table("case_dropdown_definition")
