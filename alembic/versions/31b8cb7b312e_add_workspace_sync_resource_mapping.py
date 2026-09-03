"""add workspace sync resource mapping

Revision ID: 31b8cb7b312e
Revises: 290137982547
Create Date: 2026-06-14 00:00:00.000000

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
revision: str = "31b8cb7b312e"
down_revision: str | None = "290137982547"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_sync_resource_mapping",
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
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default=sa.text("'github'"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=True),
        sa.Column("local_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_workspace_sync_resource_mapping_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_sync_resource_mapping")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "resource_type",
            "source_id",
            name="uq_workspace_sync_mapping_source",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "resource_type",
            "local_id",
            name="uq_workspace_sync_mapping_local",
        ),
    )
    op.create_index(
        op.f("ix_workspace_sync_resource_mapping_id"),
        "workspace_sync_resource_mapping",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_sync_resource_mapping_workspace_id"),
        "workspace_sync_resource_mapping",
        ["workspace_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("workspace_sync_resource_mapping"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("workspace_sync_resource_mapping"))
    op.drop_index(
        op.f("ix_workspace_sync_resource_mapping_workspace_id"),
        table_name="workspace_sync_resource_mapping",
    )
    op.drop_index(
        op.f("ix_workspace_sync_resource_mapping_id"),
        table_name="workspace_sync_resource_mapping",
    )
    op.drop_table("workspace_sync_resource_mapping")
