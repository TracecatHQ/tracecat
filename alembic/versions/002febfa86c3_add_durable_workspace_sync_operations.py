"""add durable workspace sync operations

Revision ID: 002febfa86c3
Revises: e379e38b8495
Create Date: 2026-10-05 16:30:17.288297

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
revision: str = "002febfa86c3"
down_revision: str | None = "e379e38b8495"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_sync_operation",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("artifact_key", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
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
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_workspace_sync_operation_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_sync_operation")
        ),
    )
    op.create_index(
        op.f("ix_workspace_sync_operation_id"),
        "workspace_sync_operation",
        ["id"],
        unique=True,
    )
    op.create_index(
        "ix_workspace_sync_operation_workspace_created",
        "workspace_sync_operation",
        ["workspace_id", "actor_id", "created_at", "id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("workspace_sync_operation"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("workspace_sync_operation"))
    op.drop_table("workspace_sync_operation")
