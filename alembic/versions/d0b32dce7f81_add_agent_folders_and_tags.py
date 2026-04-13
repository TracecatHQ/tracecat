"""add_agent_folders_and_tags

Revision ID: d0b32dce7f81
Revises: b742858f7d69
Create Date: 2026-04-13 10:21:23.858770

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "d0b32dce7f81"
down_revision: str | None = "b742858f7d69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_folder",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
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
            name=op.f("fk_agent_folder_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_folder")),
        sa.UniqueConstraint(
            "path", "workspace_id", name="uq_agent_folder_path_workspace"
        ),
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
            name=op.f("fk_agent_tag_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_tag")),
        sa.UniqueConstraint("name", "workspace_id", name="uq_agent_tag_name_workspace"),
        sa.UniqueConstraint("ref", "workspace_id", name="uq_agent_tag_ref_workspace"),
    )
    op.create_index(op.f("ix_agent_tag_id"), "agent_tag", ["id"], unique=True)
    op.create_index(op.f("ix_agent_tag_name"), "agent_tag", ["name"], unique=False)
    op.create_index(op.f("ix_agent_tag_ref"), "agent_tag", ["ref"], unique=False)

    op.create_table(
        "agent_tag_link",
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column("preset_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_tag_link_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["agent_tag.id"],
            name=op.f("fk_agent_tag_link_tag_id_agent_tag"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id", "preset_id", name=op.f("pk_agent_tag_link")),
    )

    op.add_column("agent_preset", sa.Column("folder_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_agent_preset_folder_id_agent_folder"),
        "agent_preset",
        "agent_folder",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(enable_workspace_table_rls("agent_folder"))
    op.execute(enable_workspace_table_rls("agent_tag"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("agent_tag"))
    op.execute(disable_workspace_table_rls("agent_folder"))
    op.drop_constraint(
        op.f("fk_agent_preset_folder_id_agent_folder"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_column("agent_preset", "folder_id")
    op.drop_table("agent_tag_link")
    op.drop_index(op.f("ix_agent_tag_ref"), table_name="agent_tag")
    op.drop_index(op.f("ix_agent_tag_name"), table_name="agent_tag")
    op.drop_index(op.f("ix_agent_tag_id"), table_name="agent_tag")
    op.drop_table("agent_tag")
    op.drop_index(op.f("ix_agent_folder_path"), table_name="agent_folder")
    op.drop_index(op.f("ix_agent_folder_id"), table_name="agent_folder")
    op.drop_table("agent_folder")
