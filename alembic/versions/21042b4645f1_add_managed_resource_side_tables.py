"""add managed resource side tables

Revision ID: 21042b4645f1
Revises: 002febfa86c3
Create Date: 2026-10-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_workspace_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "21042b4645f1"
down_revision: str | None = "002febfa86c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_columns() -> list[sa.Column]:
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
        sa.Column("rollout_operation_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_timestamp_columns(),
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
    )
    op.create_index(
        op.f("ix_managed_resource_id"), "managed_resource", ["id"], unique=True
    )
    op.execute(enable_workspace_table_rls("managed_resource"))

    op.create_table(
        "managed_target",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("pinned_tag", sa.String(length=255), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.UUID(), nullable=True),
        sa.Column("last_rollout_status", sa.String(length=32), nullable=True),
        sa.Column("last_applied_tag", sa.String(length=255), nullable=True),
        sa.Column("last_rollout_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["user.id"],
            name=op.f("fk_managed_target_assigned_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_managed_target_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_managed_target_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_managed_target")),
        sa.UniqueConstraint(
            "workspace_id", name=op.f("uq_managed_target_workspace_id")
        ),
    )
    op.create_index(op.f("ix_managed_target_id"), "managed_target", ["id"], unique=True)
    op.execute(enable_org_optional_workspace_table_rls("managed_target"))

    op.create_table(
        "managed_reference_match",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("reference_kind", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        *_timestamp_columns(),
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
            "source_name",
            name="uq_managed_reference_match_source",
        ),
    )
    op.create_index(
        op.f("ix_managed_reference_match_id"),
        "managed_reference_match",
        ["id"],
        unique=True,
    )
    op.execute(enable_workspace_table_rls("managed_reference_match"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("managed_reference_match"))
    op.drop_index(
        op.f("ix_managed_reference_match_id"), table_name="managed_reference_match"
    )
    op.drop_table("managed_reference_match")

    op.execute(disable_org_optional_workspace_table_rls("managed_target"))
    op.drop_index(op.f("ix_managed_target_id"), table_name="managed_target")
    op.drop_table("managed_target")

    op.execute(disable_workspace_table_rls("managed_resource"))
    op.drop_index(op.f("ix_managed_resource_id"), table_name="managed_resource")
    op.drop_table("managed_resource")
