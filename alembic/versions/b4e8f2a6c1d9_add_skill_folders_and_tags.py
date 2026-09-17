"""add skill folders and tags

Revision ID: b4e8f2a6c1d9
Revises: a7c3e9f1b2d4
Create Date: 2026-06-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_skill_tag_link_table_rls,
    disable_workspace_table_rls,
    enable_skill_tag_link_table_rls,
    enable_workspace_table_rls,
)

revision: str = "b4e8f2a6c1d9"
down_revision: str | None = "a7c3e9f1b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_folder",
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
            name=op.f("fk_skill_folder_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_folder")),
        sa.UniqueConstraint(
            "path", "workspace_id", name="uq_skill_folder_path_workspace"
        ),
    )
    op.create_index(op.f("ix_skill_folder_id"), "skill_folder", ["id"], unique=True)
    op.create_index(
        op.f("ix_skill_folder_path"), "skill_folder", ["path"], unique=False
    )

    op.create_table(
        "skill_tag",
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
            name=op.f("fk_skill_tag_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_tag")),
        sa.UniqueConstraint("name", "workspace_id", name="uq_skill_tag_name_workspace"),
        sa.UniqueConstraint("ref", "workspace_id", name="uq_skill_tag_ref_workspace"),
    )
    op.create_index(op.f("ix_skill_tag_id"), "skill_tag", ["id"], unique=True)
    op.create_index(op.f("ix_skill_tag_name"), "skill_tag", ["name"], unique=False)
    op.create_index(op.f("ix_skill_tag_ref"), "skill_tag", ["ref"], unique=False)

    op.create_table(
        "skill_tag_link",
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_skill_tag_link_skill_id_skill"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["skill_tag.id"],
            name=op.f("fk_skill_tag_link_tag_id_skill_tag"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id", "skill_id", name=op.f("pk_skill_tag_link")),
    )
    op.create_index(
        op.f("ix_skill_tag_link_skill_id"),
        "skill_tag_link",
        ["skill_id"],
        unique=False,
    )

    op.add_column("skill", sa.Column("folder_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_skill_folder_id_skill_folder"),
        "skill",
        "skill_folder",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_skill_workspace_folder",
        "skill",
        ["workspace_id", "folder_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("skill_folder"))
    op.execute(enable_workspace_table_rls("skill_tag"))
    op.execute(enable_skill_tag_link_table_rls())


def downgrade() -> None:
    op.execute(disable_skill_tag_link_table_rls())
    op.execute(disable_workspace_table_rls("skill_tag"))
    op.execute(disable_workspace_table_rls("skill_folder"))
    op.drop_index("ix_skill_workspace_folder", table_name="skill")
    op.drop_constraint(
        op.f("fk_skill_folder_id_skill_folder"),
        "skill",
        type_="foreignkey",
    )
    op.drop_column("skill", "folder_id")
    op.drop_index(op.f("ix_skill_tag_link_skill_id"), table_name="skill_tag_link")
    op.drop_table("skill_tag_link")
    op.drop_index(op.f("ix_skill_tag_ref"), table_name="skill_tag")
    op.drop_index(op.f("ix_skill_tag_name"), table_name="skill_tag")
    op.drop_index(op.f("ix_skill_tag_id"), table_name="skill_tag")
    op.drop_table("skill_tag")
    op.drop_index(op.f("ix_skill_folder_path"), table_name="skill_folder")
    op.drop_index(op.f("ix_skill_folder_id"), table_name="skill_folder")
    op.drop_table("skill_folder")
