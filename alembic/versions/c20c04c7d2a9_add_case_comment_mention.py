"""add case_comment_mention

Revision ID: c20c04c7d2a9
Revises: 2792569cf359
Create Date: 2026-08-07 14:29:53.887198

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "c20c04c7d2a9"
down_revision: str | None = "2792569cf359"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_comment_mention",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("comment_id", sa.UUID(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name=op.f("fk_case_comment_mention_case_id_case"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["case_comment.id"],
            name=op.f("fk_case_comment_mention_comment_id_case_comment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_comment_mention_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_case_comment_mention")),
        sa.UniqueConstraint(
            "comment_id",
            "target_type",
            "target_id",
            name=op.f("uq_case_comment_mention_comment_id_target_type_target_id"),
        ),
    )
    op.create_index(
        op.f("ix_case_comment_mention_case_id"),
        "case_comment_mention",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_comment_mention_comment_id"),
        "case_comment_mention",
        ["comment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_comment_mention_id"), "case_comment_mention", ["id"], unique=True
    )
    op.execute(enable_workspace_table_rls("case_comment_mention"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("case_comment_mention"))
    op.drop_index(op.f("ix_case_comment_mention_id"), table_name="case_comment_mention")
    op.drop_index(
        op.f("ix_case_comment_mention_comment_id"), table_name="case_comment_mention"
    )
    op.drop_index(
        op.f("ix_case_comment_mention_case_id"), table_name="case_comment_mention"
    )
    op.drop_table("case_comment_mention")
