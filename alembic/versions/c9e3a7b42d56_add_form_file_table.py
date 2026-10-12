"""Add form_file table.

Revision ID: c9e3a7b42d56
Revises: b8d2f6a31c45
Create Date: 2026-10-12 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "c9e3a7b42d56"
down_revision: str | None = "b8d2f6a31c45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "form_file",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("form_id", sa.UUID(), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=True),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("uploaded_by_service_account_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
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
            ["form_id"],
            ["form.id"],
            name=op.f("fk_form_file_form_id_form"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["form_submission.id"],
            name=op.f("fk_form_file_submission_id_form_submission"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_service_account_id"],
            ["service_account.id"],
            name=op.f("fk_form_file_uploaded_by_service_account_id_service_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["user.id"],
            name=op.f("fk_form_file_uploaded_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_form_file_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_form_file")),
    )
    op.create_index(
        "ix_form_file_form_submission_created",
        "form_file",
        ["workspace_id", "form_id", "submission_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_form_file_id"), "form_file", ["id"], unique=True)
    op.create_index(
        "ix_form_file_submission_id", "form_file", ["submission_id"], unique=False
    )
    op.execute(enable_workspace_table_rls("form_file"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("form_file"))
    op.drop_index("ix_form_file_submission_id", table_name="form_file")
    op.drop_index(op.f("ix_form_file_id"), table_name="form_file")
    op.drop_index("ix_form_file_form_submission_created", table_name="form_file")
    op.drop_table("form_file")
