"""add workspace git sync tables

Revision ID: 25f4e2a1c9d8
Revises: 9b52f7f18a31
Create Date: 2026-06-05 00:00:00.000000

"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "25f4e2a1c9d8"
down_revision: str | None = "9b52f7f18a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_SYNC_TABLES = (
    "workspace_sync_state",
    "workspace_sync_resource_mapping",
    "workspace_sync_changeset",
    "workspace_sync_changeset_item",
    "workspace_sync_materialization",
)


def _timestamps() -> list[sa.Column[datetime]]:
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


def _tenant_columns() -> list[sa.Column[uuid.UUID]]:
    return [
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _record_columns() -> list[sa.Column[uuid.UUID] | sa.Column[int]]:
    return [
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _tenant_fks(table_name: str) -> list[sa.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f(f"fk_{table_name}_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "workspace_sync_state",
        *_record_columns(),
        *_tenant_columns(),
        sa.Column(
            "provider", sa.String(length=32), server_default="git", nullable=False
        ),
        sa.Column("repo_url", sa.String(), nullable=False),
        sa.Column("target_ref", sa.String(), server_default="main", nullable=False),
        sa.Column("base_commit_sha", sa.String(), nullable=True),
        sa.Column("base_tree_sha", sa.String(), nullable=True),
        sa.Column("base_spec_hash", sa.String(), nullable=True),
        sa.Column("last_remote_commit_sha", sa.String(), nullable=True),
        sa.Column("last_remote_tree_sha", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="never_synced",
            nullable=False,
        ),
        sa.Column("last_direction", sa.String(length=16), nullable=True),
        sa.Column(
            "last_error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_fks("workspace_sync_state"),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_workspace_sync_state")),
        sa.UniqueConstraint("id", name=op.f("uq_workspace_sync_state_id")),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "repo_url",
            "target_ref",
            name="uq_workspace_sync_state_workspace_provider_repo_ref",
        ),
    )
    op.create_index(
        op.f("ix_workspace_sync_state_id"),
        "workspace_sync_state",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_sync_state_workspace_id"),
        "workspace_sync_state",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "workspace_sync_resource_mapping",
        *_record_columns(),
        *_tenant_columns(),
        sa.Column(
            "provider", sa.String(length=32), server_default="git", nullable=False
        ),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=True),
        sa.Column("local_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_synced_commit_sha", sa.String(), nullable=True),
        sa.Column("last_synced_spec_hash", sa.String(), nullable=True),
        sa.Column("last_projected_spec_hash", sa.String(), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=32),
            server_default="untracked",
            nullable=False,
        ),
        *_timestamps(),
        *_tenant_fks("workspace_sync_resource_mapping"),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_sync_resource_mapping")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_workspace_sync_resource_mapping_id")),
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

    op.create_table(
        "workspace_sync_changeset",
        *_record_columns(),
        *_tenant_columns(),
        sa.Column(
            "provider", sa.String(length=32), server_default="git", nullable=False
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("base_commit_sha", sa.String(), nullable=True),
        sa.Column("base_spec_hash", sa.String(), nullable=True),
        sa.Column(
            "selected_resources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "selected_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "rendered_files",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "validation_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "validation_result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=32), server_default="open", nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        *_tenant_fks("workspace_sync_changeset"),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_sync_changeset")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_workspace_sync_changeset_id")),
    )
    op.create_index(
        op.f("ix_workspace_sync_changeset_id"),
        "workspace_sync_changeset",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_sync_changeset_workspace_id"),
        "workspace_sync_changeset",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "workspace_sync_changeset_item",
        *_record_columns(),
        *_tenant_columns(),
        sa.Column("changeset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=True),
        sa.Column("local_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("spec_hash", sa.String(), nullable=True),
        sa.Column(
            "dependencies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        *_tenant_fks("workspace_sync_changeset_item"),
        sa.ForeignKeyConstraint(
            ["changeset_id"],
            ["workspace_sync_changeset.id"],
            name=op.f(
                "fk_workspace_sync_changeset_item_changeset_id_workspace_sync_changeset"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_sync_changeset_item")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_workspace_sync_changeset_item_id")),
        sa.UniqueConstraint(
            "changeset_id",
            "resource_type",
            "source_id",
            name="uq_workspace_sync_changeset_item_resource",
        ),
    )
    op.create_index(
        op.f("ix_workspace_sync_changeset_item_id"),
        "workspace_sync_changeset_item",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_sync_changeset_item_workspace_id"),
        "workspace_sync_changeset_item",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_sync_changeset_item_changeset_id"),
        "workspace_sync_changeset_item",
        ["changeset_id"],
        unique=False,
    )

    op.create_table(
        "workspace_sync_materialization",
        *_record_columns(),
        *_tenant_columns(),
        sa.Column("changeset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider", sa.String(length=32), server_default="git", nullable=False
        ),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("base_ref", sa.String(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(), nullable=True),
        sa.Column(
            "commit_shas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_timestamps(),
        *_tenant_fks("workspace_sync_materialization"),
        sa.ForeignKeyConstraint(
            ["changeset_id"],
            ["workspace_sync_changeset.id"],
            name=op.f(
                "fk_workspace_sync_materialization_changeset_id_workspace_sync_changeset"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_sync_materialization")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_workspace_sync_materialization_id")),
    )
    op.create_index(
        op.f("ix_workspace_sync_materialization_id"),
        "workspace_sync_materialization",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_sync_materialization_workspace_id"),
        "workspace_sync_materialization",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_sync_materialization_changeset_id"),
        "workspace_sync_materialization",
        ["changeset_id"],
        unique=False,
    )

    for table in WORKSPACE_SYNC_TABLES:
        op.execute(enable_workspace_table_rls(table))


def downgrade() -> None:
    for table in reversed(WORKSPACE_SYNC_TABLES):
        op.execute(disable_workspace_table_rls(table))

    op.drop_table("workspace_sync_materialization")
    op.drop_table("workspace_sync_changeset_item")
    op.drop_table("workspace_sync_changeset")
    op.drop_table("workspace_sync_resource_mapping")
    op.drop_table("workspace_sync_state")
