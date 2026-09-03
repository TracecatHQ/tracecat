"""Add case versions.

Revision ID: 598b32358ec5
Revises: 35fa9fc71258
Create Date: 2026-08-19 17:33:35.727844

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
revision: str = "598b32358ec5"
down_revision: str | None = "35fa9fc71258"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sa.Enum("SUMMARY", "DESCRIPTION", name="caseversionfield").create(op.get_bind())
    op.create_table(
        "case_version",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column(
            "field",
            postgresql.ENUM(
                "SUMMARY",
                "DESCRIPTION",
                name="caseversionfield",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_case_version_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name=op.f("fk_case_version_case_id_case"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_case_version_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_version_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_case_version")),
        sa.UniqueConstraint(
            "workspace_id",
            "case_id",
            "field",
            "version",
            name="uq_case_version_workspace_case_field_version",
        ),
    )
    op.create_index(
        "ix_case_version_case_field_timeline",
        "case_version",
        ["workspace_id", "case_id", "field", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_version_case_id"),
        "case_version",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_version_case_timeline",
        "case_version",
        ["workspace_id", "case_id", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_version_id"),
        "case_version",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_case_version_user_id"),
        "case_version",
        ["user_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO case_version (
                id,
                case_id,
                field,
                version,
                content,
                user_id,
                workspace_id,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                case_row.id,
                baseline.field::caseversionfield,
                1,
                baseline.content,
                NULL,
                case_row.workspace_id,
                case_row.updated_at,
                case_row.updated_at
            FROM "case" AS case_row
            CROSS JOIN LATERAL (
                VALUES
                    ('SUMMARY', case_row.summary),
                    ('DESCRIPTION', case_row.description)
            ) AS baseline(field, content)
            """
        )
    )
    op.execute(enable_workspace_table_rls("case_version"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("case_version"))
    op.drop_table("case_version")
    sa.Enum("SUMMARY", "DESCRIPTION", name="caseversionfield").drop(op.get_bind())
