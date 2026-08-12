"""add case comment agent invocation

Revision ID: 35fa9fc71258
Revises: c20c04c7d2a9
Create Date: 2026-08-10 19:33:35.759528

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
revision: str = "35fa9fc71258"
down_revision: str | None = "c20c04c7d2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_comment_agent_invocation",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mention_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("reply_comment_id", sa.UUID(), nullable=True),
        sa.Column("preset_name", sa.String(length=120), nullable=False),
        sa.Column("preset_slug", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["mention_id"],
            ["case_comment_mention.id"],
            name=op.f(
                "fk_case_comment_agent_invocation_mention_id_case_comment_mention"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reply_comment_id"],
            ["case_comment.id"],
            name=op.f("fk_case_comment_agent_invocation_reply_comment_id_case_comment"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name=op.f("fk_case_comment_agent_invocation_session_id_agent_session"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_comment_agent_invocation_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_case_comment_agent_invocation")
        ),
        sa.UniqueConstraint(
            "mention_id", name=op.f("uq_case_comment_agent_invocation_mention_id")
        ),
    )
    for column, unique in (
        ("id", True),
        ("reply_comment_id", False),
        ("session_id", False),
        ("status", False),
    ):
        op.create_index(
            op.f(f"ix_case_comment_agent_invocation_{column}"),
            "case_comment_agent_invocation",
            [column],
            unique=unique,
        )
    op.execute(enable_workspace_table_rls("case_comment_agent_invocation"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("case_comment_agent_invocation"))
    op.drop_table("case_comment_agent_invocation")
