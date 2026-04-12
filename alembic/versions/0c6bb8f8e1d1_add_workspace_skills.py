"""add workspace skills

Revision ID: 0c6bb8f8e1d1
Revises: 7e1a4d9c2b6f
Create Date: 2026-04-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "0c6bb8f8e1d1"
down_revision: str | None = "b742858f7d69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
        sa.Column(
            "draft_revision",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            name=op.f("fk_skill_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill")),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_skill_workspace_slug"),
    )
    op.create_index(op.f("ix_skill_id"), "skill", ["id"], unique=True)
    op.create_index(op.f("ix_skill_slug"), "skill", ["slug"], unique=False)

    op.create_table(
        "skill_blob",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
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
            name=op.f("fk_skill_blob_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_blob")),
        sa.UniqueConstraint(
            "workspace_id",
            "sha256",
            "content_type",
            name="uq_skill_blob_workspace_sha256_content_type",
        ),
    )
    op.create_index(op.f("ix_skill_blob_id"), "skill_blob", ["id"], unique=True)
    op.create_index(
        op.f("ix_skill_blob_sha256"), "skill_blob", ["sha256"], unique=False
    )

    op.create_table(
        "skill_version",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_size_bytes", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
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
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_skill_version_skill_id_skill"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_version_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_version")),
        sa.UniqueConstraint(
            "workspace_id",
            "skill_id",
            "version",
            name="uq_skill_version_workspace_skill_version",
        ),
    )
    op.create_index(op.f("ix_skill_version_id"), "skill_version", ["id"], unique=True)
    op.create_index(
        op.f("ix_skill_version_skill_id"), "skill_version", ["skill_id"], unique=False
    )

    op.create_foreign_key(
        op.f("fk_skill_current_version_id_skill_version"),
        "skill",
        "skill_version",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "skill_upload",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("blob_id", sa.UUID(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            ["blob_id"],
            ["skill_blob.id"],
            name=op.f("fk_skill_upload_blob_id_skill_blob"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_skill_upload_skill_id_skill"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_upload_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_upload")),
    )
    op.create_index(op.f("ix_skill_upload_id"), "skill_upload", ["id"], unique=True)
    op.create_index(
        op.f("ix_skill_upload_skill_id"), "skill_upload", ["skill_id"], unique=False
    )

    op.create_table(
        "skill_draft_file",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("blob_id", sa.UUID(), nullable=False),
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
            ["blob_id"],
            ["skill_blob.id"],
            name=op.f("fk_skill_draft_file_blob_id_skill_blob"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_skill_draft_file_skill_id_skill"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_draft_file_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_draft_file")),
        sa.UniqueConstraint(
            "workspace_id",
            "skill_id",
            "path",
            name="uq_skill_draft_file_workspace_skill_path",
        ),
    )
    op.create_index(
        op.f("ix_skill_draft_file_id"), "skill_draft_file", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_skill_draft_file_skill_id"),
        "skill_draft_file",
        ["skill_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_skill_draft_file_blob_id"),
        "skill_draft_file",
        ["blob_id"],
        unique=False,
    )

    op.create_table(
        "skill_version_file",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_version_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("blob_id", sa.UUID(), nullable=False),
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
            ["blob_id"],
            ["skill_blob.id"],
            name=op.f("fk_skill_version_file_blob_id_skill_blob"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_version.id"],
            name=op.f("fk_skill_version_file_skill_version_id_skill_version"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_version_file_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_version_file")),
        sa.UniqueConstraint(
            "workspace_id",
            "skill_version_id",
            "path",
            name="uq_skill_version_file_workspace_version_path",
        ),
    )
    op.create_index(
        op.f("ix_skill_version_file_id"), "skill_version_file", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_skill_version_file_skill_version_id"),
        "skill_version_file",
        ["skill_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_skill_version_file_blob_id"),
        "skill_version_file",
        ["blob_id"],
        unique=False,
    )

    op.create_table(
        "agent_preset_skill",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("preset_id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("skill_version_id", sa.UUID(), nullable=False),
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
            ["skill_version_id"],
            ["skill_version.id"],
            name=op.f("fk_agent_preset_skill_skill_version_id_skill_version"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_preset_skill_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_agent_preset_skill_skill_id_skill"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_skill_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_preset_skill")),
        sa.UniqueConstraint(
            "workspace_id",
            "preset_id",
            "skill_id",
            name="uq_agent_preset_skill_workspace_preset_skill",
        ),
    )
    op.create_index(
        op.f("ix_agent_preset_skill_id"), "agent_preset_skill", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_agent_preset_skill_preset_id"),
        "agent_preset_skill",
        ["preset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_skill_skill_id"),
        "agent_preset_skill",
        ["skill_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_skill_skill_version_id"),
        "agent_preset_skill",
        ["skill_version_id"],
        unique=False,
    )

    op.create_table(
        "agent_preset_version_skill",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("preset_version_id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("skill_version_id", sa.UUID(), nullable=False),
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
            ["preset_version_id"],
            ["agent_preset_version.id"],
            name=op.f(
                "fk_agent_preset_version_skill_preset_version_id_agent_preset_version"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skill.id"],
            name=op.f("fk_agent_preset_version_skill_skill_id_skill"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_version.id"],
            name=op.f("fk_agent_preset_version_skill_skill_version_id_skill_version"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_version_skill_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_agent_preset_version_skill")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "preset_version_id",
            "skill_id",
            name="uq_agent_preset_version_skill_workspace_version_skill",
        ),
    )
    op.create_index(
        op.f("ix_agent_preset_version_skill_id"),
        "agent_preset_version_skill",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_preset_version_skill_preset_version_id"),
        "agent_preset_version_skill",
        ["preset_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_version_skill_skill_id"),
        "agent_preset_version_skill",
        ["skill_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_version_skill_skill_version_id"),
        "agent_preset_version_skill",
        ["skill_version_id"],
        unique=False,
    )

    for table in (
        "skill",
        "skill_blob",
        "skill_upload",
        "skill_draft_file",
        "skill_version",
        "skill_version_file",
        "agent_preset_skill",
        "agent_preset_version_skill",
    ):
        op.execute(enable_workspace_table_rls(table))


def downgrade() -> None:
    for table in (
        "agent_preset_version_skill",
        "agent_preset_skill",
        "skill_version_file",
        "skill_draft_file",
        "skill_upload",
        "skill_version",
        "skill_blob",
        "skill",
    ):
        op.execute(disable_workspace_table_rls(table))

    op.drop_index(
        op.f("ix_agent_preset_version_skill_skill_version_id"),
        table_name="agent_preset_version_skill",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_skill_skill_id"),
        table_name="agent_preset_version_skill",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_skill_preset_version_id"),
        table_name="agent_preset_version_skill",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_skill_id"),
        table_name="agent_preset_version_skill",
    )
    op.drop_table("agent_preset_version_skill")

    op.execute(
        sa.text(
            f"DROP INDEX IF EXISTS {op.f('ix_agent_preset_skill_skill_version_id')}"
        )
    )
    op.drop_index(
        op.f("ix_agent_preset_skill_skill_id"),
        table_name="agent_preset_skill",
    )
    op.drop_index(
        op.f("ix_agent_preset_skill_preset_id"),
        table_name="agent_preset_skill",
    )
    op.drop_index(op.f("ix_agent_preset_skill_id"), table_name="agent_preset_skill")
    op.drop_table("agent_preset_skill")

    op.drop_index(
        op.f("ix_skill_version_file_blob_id"),
        table_name="skill_version_file",
    )
    op.drop_index(
        op.f("ix_skill_version_file_skill_version_id"),
        table_name="skill_version_file",
    )
    op.drop_index(op.f("ix_skill_version_file_id"), table_name="skill_version_file")
    op.drop_table("skill_version_file")

    op.drop_index(
        op.f("ix_skill_draft_file_blob_id"),
        table_name="skill_draft_file",
    )
    op.drop_index(
        op.f("ix_skill_draft_file_skill_id"),
        table_name="skill_draft_file",
    )
    op.drop_index(op.f("ix_skill_draft_file_id"), table_name="skill_draft_file")
    op.drop_table("skill_draft_file")

    op.drop_index(op.f("ix_skill_upload_skill_id"), table_name="skill_upload")
    op.drop_index(op.f("ix_skill_upload_id"), table_name="skill_upload")
    op.drop_table("skill_upload")

    op.drop_constraint(
        op.f("fk_skill_current_version_id_skill_version"),
        "skill",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_skill_version_skill_id"), table_name="skill_version")
    op.drop_index(op.f("ix_skill_version_id"), table_name="skill_version")
    op.drop_table("skill_version")

    op.drop_index(op.f("ix_skill_blob_sha256"), table_name="skill_blob")
    op.drop_index(op.f("ix_skill_blob_id"), table_name="skill_blob")
    op.drop_table("skill_blob")

    op.drop_index(op.f("ix_skill_slug"), table_name="skill")
    op.drop_index(op.f("ix_skill_id"), table_name="skill")
    op.drop_table("skill")
