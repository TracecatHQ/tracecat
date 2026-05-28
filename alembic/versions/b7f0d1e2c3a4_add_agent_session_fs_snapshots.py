"""add_agent_session_fs_snapshots

Revision ID: b7f0d1e2c3a4
Revises: a3d7c9e8b4f2
Create Date: 2026-05-28 03:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "b7f0d1e2c3a4"
down_revision: str | None = "a3d7c9e8b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_session_fs_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uncompressed_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("archive_format", sa.String(length=32), nullable=False),
        sa.Column("compression", sa.String(length=32), nullable=False),
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
            ["session_id"],
            ["agent_session.id"],
            name=op.f("fk_agent_session_fs_snapshot_session_id_agent_session"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_session_fs_snapshot_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id",
            name=op.f("pk_agent_session_fs_snapshot"),
        ),
    )
    op.create_index(
        op.f("ix_agent_session_fs_snapshot_id"),
        "agent_session_fs_snapshot",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_session_fs_snapshot_session_id"),
        "agent_session_fs_snapshot",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_session_fs_snapshot_session_created",
        "agent_session_fs_snapshot",
        ["session_id", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_session_fs_snapshot_sha256"),
        "agent_session_fs_snapshot",
        ["sha256"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("agent_session_fs_snapshot"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("agent_session_fs_snapshot"))
    op.drop_index(
        op.f("ix_agent_session_fs_snapshot_sha256"),
        table_name="agent_session_fs_snapshot",
    )
    op.drop_index(
        "ix_agent_session_fs_snapshot_session_created",
        table_name="agent_session_fs_snapshot",
    )
    op.drop_index(
        op.f("ix_agent_session_fs_snapshot_session_id"),
        table_name="agent_session_fs_snapshot",
    )
    op.drop_index(
        op.f("ix_agent_session_fs_snapshot_id"),
        table_name="agent_session_fs_snapshot",
    )
    op.drop_table("agent_session_fs_snapshot")
