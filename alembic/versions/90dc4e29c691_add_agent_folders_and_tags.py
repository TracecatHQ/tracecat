"""add agent folders and tags

Revision ID: 90dc4e29c691
Revises: 8b2f6a9c4d10
Create Date: 2026-05-05 00:00:00.000000

Adds the agent_folder, agent_tag, and agent_tag_link tables, plus a nullable
folder_id column on agent_preset. Schema mirrors workflow_folder /
workflow_tag / workflow_tag_link 1:1 (materialized-path folders, slug-based
tag refs, M2M link table) with one extra layer of safety: cross-references
between workspace-scoped tables use composite foreign keys that include
workspace_id, so a row in workspace A can never reference a row in
workspace B even if a stray UUID is supplied. This mirrors the pattern
already used for agent_catalog -> agent_custom_provider at the org tier.

Concretely:
- agent_folder, agent_tag, and agent_preset each carry a
  UNIQUE (workspace_id, id) constraint so children can target it.
- agent_preset.folder_id is enforced via a composite FK
  (workspace_id, folder_id) -> agent_folder (workspace_id, id).
- agent_tag_link carries its own NOT NULL workspace_id column with composite
  FKs to both agent_tag and agent_preset, ensuring the linked tag and
  preset always live in the same workspace.

This migration is purely additive: every change is a new table, a new
nullable column, or a new constraint that existing rows already satisfy
(since `id` is unique table-wide, `(workspace_id, id)` is unique too). Old
application versions remain functional after upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "90dc4e29c691"
down_revision: str | None = "8b2f6a9c4d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_folder",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            name=op.f("fk_agent_folder_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_folder")),
        sa.UniqueConstraint(
            "path", "workspace_id", name="uq_agent_folder_path_workspace"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_folder_workspace_id"),
    )
    op.create_index(op.f("ix_agent_folder_id"), "agent_folder", ["id"], unique=True)
    op.create_index(
        op.f("ix_agent_folder_path"), "agent_folder", ["path"], unique=False
    )

    op.create_table(
        "agent_tag",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            name=op.f("fk_agent_tag_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_tag")),
        sa.UniqueConstraint("name", "workspace_id", name="uq_agent_tag_name_workspace"),
        sa.UniqueConstraint("ref", "workspace_id", name="uq_agent_tag_ref_workspace"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_agent_tag_workspace_id"),
    )
    op.create_index(op.f("ix_agent_tag_id"), "agent_tag", ["id"], unique=True)
    op.create_index(op.f("ix_agent_tag_name"), "agent_tag", ["name"], unique=False)
    op.create_index(op.f("ix_agent_tag_ref"), "agent_tag", ["ref"], unique=False)

    # agent_preset already exists; add a workspace-aware unique so children
    # (agent_tag_link, agent_preset.folder_id) can target it via composite FK.
    # Existing rows are unique on `id` alone, so this constraint is satisfied
    # at creation time without backfill.
    op.create_unique_constraint(
        "uq_agent_preset_workspace_id",
        "agent_preset",
        ["workspace_id", "id"],
    )

    op.create_table(
        "agent_tag_link",
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column("preset_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "preset_id"],
            ["agent_preset.workspace_id", "agent_preset.id"],
            name=op.f("fk_agent_tag_link_preset_workspace"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tag_id"],
            ["agent_tag.workspace_id", "agent_tag.id"],
            name=op.f("fk_agent_tag_link_tag_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id", "preset_id", name=op.f("pk_agent_tag_link")),
    )

    op.add_column(
        "agent_preset",
        sa.Column("folder_id", sa.UUID(), nullable=True),
    )
    # Composite FK enforces same-workspace folder reference. ON DELETE
    # NO ACTION rather than SET NULL: a composite SET NULL would also null
    # workspace_id, violating its NOT NULL constraint. The folder service
    # is responsible for detaching presets before deleting a folder
    # (delete_folder in tracecat/agent/folders/service.py).
    op.create_foreign_key(
        op.f("fk_agent_preset_folder_workspace"),
        "agent_preset",
        "agent_folder",
        ["workspace_id", "folder_id"],
        ["workspace_id", "id"],
        ondelete="NO ACTION",
    )
    op.create_index(
        op.f("ix_agent_preset_folder_id"),
        "agent_preset",
        ["folder_id"],
        unique=False,
    )

    for table in ("agent_folder", "agent_tag", "agent_tag_link"):
        op.execute(enable_workspace_table_rls(table))


def downgrade() -> None:
    for table in ("agent_tag_link", "agent_tag", "agent_folder"):
        op.execute(disable_workspace_table_rls(table))

    op.drop_index(op.f("ix_agent_preset_folder_id"), table_name="agent_preset")
    op.drop_constraint(
        op.f("fk_agent_preset_folder_workspace"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_column("agent_preset", "folder_id")

    op.drop_table("agent_tag_link")

    op.drop_constraint("uq_agent_preset_workspace_id", "agent_preset", type_="unique")

    op.drop_index(op.f("ix_agent_tag_ref"), table_name="agent_tag")
    op.drop_index(op.f("ix_agent_tag_name"), table_name="agent_tag")
    op.drop_index(op.f("ix_agent_tag_id"), table_name="agent_tag")
    op.drop_table("agent_tag")

    op.drop_index(op.f("ix_agent_folder_path"), table_name="agent_folder")
    op.drop_index(op.f("ix_agent_folder_id"), table_name="agent_folder")
    op.drop_table("agent_folder")
