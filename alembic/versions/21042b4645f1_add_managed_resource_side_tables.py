"""add managed resource side tables

Revision ID: 21042b4645f1
Revises: e4fd31674ae9
Create Date: 2026-10-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_read_only_table_rls,
    enable_workspace_read_only_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "21042b4645f1"
down_revision: str | None = "e4fd31674ae9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _record_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "managed_resource",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("local_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=False),
        sa.Column("deployed_tag", sa.String(length=255), nullable=False),
        sa.Column("deployed_commit_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "deployed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_managed_resource_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_managed_resource")),
        sa.UniqueConstraint(
            "workspace_id",
            "resource_type",
            "local_id",
            name="uq_managed_resource_local",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "resource_type",
            "source_id",
            name="uq_managed_resource_source",
        ),
    )
    op.create_index(
        op.f("ix_managed_resource_id"), "managed_resource", ["id"], unique=True
    )
    op.execute(enable_workspace_read_only_table_rls("managed_resource"))

    op.create_table(
        "managed_target",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pinned_tag", sa.String(length=255), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.UUID(), nullable=True),
        sa.Column(
            "last_rollout_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("last_applied_tag", sa.String(length=255), nullable=True),
        sa.Column("last_rollout_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["user.id"],
            name=op.f("fk_managed_target_assigned_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_managed_target_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_managed_target")),
        sa.UniqueConstraint("workspace_id", name="uq_managed_target_workspace_id"),
    )
    op.create_index(op.f("ix_managed_target_id"), "managed_target", ["id"], unique=True)
    op.execute(enable_workspace_read_only_table_rls("managed_target"))

    op.create_table(
        "managed_reference_match",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("reference_kind", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("resolved_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_record_columns(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_managed_reference_match_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_managed_reference_match")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "reference_kind",
            "source_key",
            name="uq_managed_reference_match_source",
        ),
    )
    op.create_index(
        op.f("ix_managed_reference_match_id"),
        "managed_reference_match",
        ["id"],
        unique=True,
    )
    op.execute(enable_workspace_read_only_table_rls("managed_reference_match"))


def downgrade() -> None:
    for table in ("managed_reference_match", "managed_target", "managed_resource"):
        op.execute(disable_workspace_read_only_table_rls(table))
        op.drop_index(op.f(f"ix_{table}_id"), table_name=table)
        op.drop_table(table)
