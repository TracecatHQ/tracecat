"""add managed rollout, preset skill binding and detached copy tables

Revision ID: 4a17af960e05
Revises: 8c04c3aa6e35
Create Date: 2026-10-29 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_read_only_table_rls,
    disable_workspace_table_rls,
    enable_workspace_read_only_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "4a17af960e05"
down_revision: str | None = "8c04c3aa6e35"
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


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.id"],
        name=op.f(f"fk_{table}_workspace_id_workspace"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "managed_rollout",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tag", sa.String(length=255), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("preview", postgresql.JSONB(), nullable=True),
        sa.Column("state_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_record_columns(),
        _workspace_fk("managed_rollout"),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["managed_target.id"],
            name=op.f("fk_managed_rollout_target_id_managed_target"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_managed_rollout_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_managed_rollout")),
    )
    op.create_index(
        op.f("ix_managed_rollout_id"), "managed_rollout", ["id"], unique=True
    )
    op.execute(enable_workspace_read_only_table_rls("managed_rollout"))

    op.add_column("managed_resource", sa.Column("rollout_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_managed_resource_rollout_id_managed_rollout"),
        "managed_resource",
        "managed_rollout",
        ["rollout_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "managed_preset_skill_binding",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("preset_id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_record_columns(),
        _workspace_fk("managed_preset_skill_binding"),
        sa.ForeignKeyConstraint(
            ["preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_managed_preset_skill_binding_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_managed_preset_skill_binding_skill_id_skill"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_managed_preset_skill_binding")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "preset_id",
            "skill_id",
            name="uq_managed_preset_skill_binding",
        ),
    )
    op.create_index(
        op.f("ix_managed_preset_skill_binding_id"),
        "managed_preset_skill_binding",
        ["id"],
        unique=True,
    )
    op.execute(enable_workspace_read_only_table_rls("managed_preset_skill_binding"))

    op.create_table(
        "managed_detached_copy",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("local_id", sa.UUID(), nullable=False),
        sa.Column("original_local_id", sa.UUID(), nullable=False),
        sa.Column("original_source_id", sa.String(), nullable=False),
        sa.Column("original_name", sa.String(), nullable=False),
        sa.Column("detached_by", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_record_columns(),
        _workspace_fk("managed_detached_copy"),
        sa.ForeignKeyConstraint(
            ["detached_by"],
            ["user.id"],
            name=op.f("fk_managed_detached_copy_detached_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_managed_detached_copy")),
        sa.UniqueConstraint(
            "workspace_id",
            "resource_type",
            "local_id",
            name="uq_managed_detached_copy_local",
        ),
    )
    op.create_index(
        op.f("ix_managed_detached_copy_id"),
        "managed_detached_copy",
        ["id"],
        unique=True,
    )
    op.execute(enable_workspace_table_rls("managed_detached_copy"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("managed_detached_copy"))
    op.drop_index(
        op.f("ix_managed_detached_copy_id"), table_name="managed_detached_copy"
    )
    op.drop_table("managed_detached_copy")

    op.execute(disable_workspace_read_only_table_rls("managed_preset_skill_binding"))
    op.drop_index(
        op.f("ix_managed_preset_skill_binding_id"),
        table_name="managed_preset_skill_binding",
    )
    op.drop_table("managed_preset_skill_binding")

    op.drop_constraint(
        op.f("fk_managed_resource_rollout_id_managed_rollout"),
        "managed_resource",
        type_="foreignkey",
    )
    op.drop_column("managed_resource", "rollout_id")

    op.execute(disable_workspace_read_only_table_rls("managed_rollout"))
    op.drop_index(op.f("ix_managed_rollout_id"), table_name="managed_rollout")
    op.drop_table("managed_rollout")
