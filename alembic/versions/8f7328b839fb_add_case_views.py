"""add case views

Revision ID: 8f7328b839fb
Revises: ec392d96f2b0
Create Date: 2026-11-13 00:00:00.000000

Additive only: creates the saved case view tables. The previous app version
never reads them, so it keeps working after this upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "8f7328b839fb"
down_revision: str | None = "ec392d96f2b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "case_view",
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "visibility",
            sa.Enum(
                "personal",
                "workspace",
                name="caseviewvisibility",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "query_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("order_by", sa.String(length=32), nullable=True),
        sa.Column("sort", sa.String(length=4), nullable=True),
        sa.Column("columns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name=op.f("fk_case_view_owner_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_view_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_case_view")),
    )
    op.create_index(op.f("ix_case_view_id"), "case_view", ["id"], unique=True)
    op.create_index(
        "ix_case_view_workspace_visibility",
        "case_view",
        ["workspace_id", "visibility"],
        unique=False,
    )
    op.create_index(
        "ix_case_view_workspace_owner",
        "case_view",
        ["workspace_id", "owner_id"],
        unique=False,
    )

    op.create_table(
        "case_view_pin",
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("view_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_case_view_pin_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["view_id"],
            ["case_view.id"],
            name=op.f("fk_case_view_pin_view_id_case_view"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_view_pin_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_case_view_pin")),
        sa.UniqueConstraint("user_id", "view_id", name="uq_case_view_pin_user_view"),
    )
    op.create_index(
        "ix_case_view_pin_workspace_user",
        "case_view_pin",
        ["workspace_id", "user_id"],
        unique=False,
    )

    op.execute(enable_workspace_table_rls("case_view"))
    op.execute(enable_workspace_table_rls("case_view_pin"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("case_view_pin"))
    op.execute(disable_workspace_table_rls("case_view"))
    op.drop_index("ix_case_view_pin_workspace_user", table_name="case_view_pin")
    op.drop_table("case_view_pin")
    op.drop_index("ix_case_view_workspace_owner", table_name="case_view")
    op.drop_index("ix_case_view_workspace_visibility", table_name="case_view")
    op.drop_index(op.f("ix_case_view_id"), table_name="case_view")
    op.drop_table("case_view")
