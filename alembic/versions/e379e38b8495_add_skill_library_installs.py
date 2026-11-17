"""add skill library installs

Revision ID: e379e38b8495
Revises: d45011587673
Create Date: 2026-09-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "e379e38b8495"
down_revision: str | None = "d45011587673"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_library_install",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("library_slug", sa.String(length=64), nullable=False),
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
            name=op.f("fk_skill_library_install_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_library_install")),
        sa.UniqueConstraint(
            "workspace_id",
            "library_slug",
            name="uq_skill_library_install_workspace_slug",
        ),
    )
    op.create_index(
        op.f("ix_skill_library_install_id"),
        "skill_library_install",
        ["id"],
        unique=True,
    )
    op.add_column(
        "agent_preset",
        sa.Column(
            "library_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "agent_preset_version",
        sa.Column(
            "library_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.execute(enable_workspace_table_rls("skill_library_install"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("skill_library_install"))
    op.drop_column("agent_preset_version", "library_skills")
    op.drop_column("agent_preset", "library_skills")
    op.drop_index(
        op.f("ix_skill_library_install_id"), table_name="skill_library_install"
    )
    op.drop_table("skill_library_install")
