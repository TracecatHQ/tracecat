"""add agent preset approvers

Revision ID: 997fd6b8f9f4
Revises: 94caf98ad60f
Create Date: 2026-11-17 00:00:00.000000

Additive only: creates the agent_preset_approver table and adds the nullable
approval.agent_preset_id. The previous app version never reads either, so it
keeps working after this upgrade.
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
revision: str = "997fd6b8f9f4"
down_revision: str | None = "94caf98ad60f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_preset_approver",
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
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(user_id, group_id, role_id) = 1",
            name=op.f("ck_agent_preset_approver_one_principal"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["group.id"],
            name=op.f("fk_agent_preset_approver_group_id_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["preset_id", "workspace_id"],
            ["agent_preset.id", "agent_preset.workspace_id"],
            name=op.f("fk_agent_preset_approver_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_agent_preset_approver_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_agent_preset_approver_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_approver_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_preset_approver")),
    )
    op.create_index(
        op.f("ix_agent_preset_approver_id"),
        "agent_preset_approver",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_preset_approver_group_id"),
        "agent_preset_approver",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_approver_role_id"),
        "agent_preset_approver",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_approver_user_id"),
        "agent_preset_approver",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_agent_preset_approver_preset_principal",
        "agent_preset_approver",
        ["preset_id", "user_id", "group_id", "role_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.execute(enable_workspace_table_rls("agent_preset_approver"))
    # Fail fast instead of queueing approval reads behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "approval",
        sa.Column("agent_preset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # NOT VALID skips the table scan under ACCESS EXCLUSIVE; existing rows are NULL.
    op.execute(
        "ALTER TABLE approval ADD CONSTRAINT fk_approval_agent_preset_id_agent_preset "
        "FOREIGN KEY (agent_preset_id) REFERENCES agent_preset (id) "
        "ON DELETE SET NULL NOT VALID"
    )
    # Deleting a preset nulls its approvals; without this each delete scans the table.
    op.create_index(
        "ix_approval_agent_preset_id",
        "approval",
        ["agent_preset_id"],
        unique=False,
        postgresql_where=sa.text("agent_preset_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_approval_agent_preset_id", table_name="approval")
    op.drop_constraint(
        op.f("fk_approval_agent_preset_id_agent_preset"),
        "approval",
        type_="foreignkey",
    )
    op.drop_column("approval", "agent_preset_id")
    op.execute(disable_workspace_table_rls("agent_preset_approver"))
    op.drop_index(
        "uq_agent_preset_approver_preset_principal",
        table_name="agent_preset_approver",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(
        op.f("ix_agent_preset_approver_user_id"), table_name="agent_preset_approver"
    )
    op.drop_index(
        op.f("ix_agent_preset_approver_role_id"), table_name="agent_preset_approver"
    )
    op.drop_index(
        op.f("ix_agent_preset_approver_group_id"), table_name="agent_preset_approver"
    )
    op.drop_index(
        op.f("ix_agent_preset_approver_id"), table_name="agent_preset_approver"
    )
    op.drop_table("agent_preset_approver")
