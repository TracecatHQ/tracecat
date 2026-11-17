"""Add forms: workflow.expects_order and the form tables.

Revision ID: 7e666328c9a6
Revises: 214ec9659a86
Create Date: 2026-10-13 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import SchemaItem

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "7e666328c9a6"
down_revision: str | None = "214ec9659a86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Parents first, so downgrade can walk the tuple in reverse.
_TABLES = ("form", "form_submission", "form_view", "form_file")


def _create_table(table: str, *items: SchemaItem) -> None:
    """Create a workspace-owned table with RLS and a unique index on ``id``.

    The index comes before any child table, whose foreign keys need it.
    """
    op.create_table(
        table,
        sa.Column("id", sa.UUID(), nullable=False),
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
        _foreign_key(table, "workspace_id", "workspace", "CASCADE"),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f(f"pk_{table}")),
        *items,
    )
    op.create_index(op.f(f"ix_{table}_id"), table, ["id"], unique=True)
    op.execute(enable_workspace_table_rls(table))


def _foreign_key(
    table: str, column: str, target: str, ondelete: str
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.add_column(
        "workflow",
        sa.Column(
            "expects_order", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    _create_table(
        "form",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("success_message", sa.String(), nullable=True),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _foreign_key("form", "workflow_id", "workflow", "CASCADE"),
        sa.UniqueConstraint("workflow_id", name=op.f("uq_form_workflow_id")),
    )
    _create_table(
        "form_submission",
        sa.Column("form_id", sa.UUID(), nullable=False),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("submitted_by_service_account_id", sa.UUID(), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "visible_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("wf_exec_id", sa.String(), nullable=False),
        _foreign_key("form_submission", "form_id", "form", "CASCADE"),
        _foreign_key("form_submission", "submitted_by_user_id", "user", "SET NULL"),
        _foreign_key(
            "form_submission",
            "submitted_by_service_account_id",
            "service_account",
            "SET NULL",
        ),
    )
    op.create_index(
        "ix_form_submission_form_created",
        "form_submission",
        ["workspace_id", "form_id", "created_at", "surrogate_id"],
    )
    op.create_index(
        "ix_form_submission_user_created",
        "form_submission",
        ["submitted_by_user_id", "created_at", "surrogate_id"],
    )
    _create_table(
        "form_view",
        sa.Column("form_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        _foreign_key("form_view", "form_id", "form", "CASCADE"),
        _foreign_key("form_view", "user_id", "user", "SET NULL"),
        sa.UniqueConstraint(
            "workspace_id",
            "form_id",
            "user_id",
            "window_start",
            name="uq_form_view_form_user_window",
        ),
    )
    _create_table(
        "form_file",
        sa.Column("form_id", sa.UUID(), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=True),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("uploaded_by_service_account_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        _foreign_key("form_file", "form_id", "form", "CASCADE"),
        _foreign_key("form_file", "submission_id", "form_submission", "CASCADE"),
        _foreign_key("form_file", "uploaded_by_user_id", "user", "SET NULL"),
        _foreign_key(
            "form_file",
            "uploaded_by_service_account_id",
            "service_account",
            "SET NULL",
        ),
    )
    op.create_index(
        "ix_form_file_form_submission_created",
        "form_file",
        ["workspace_id", "form_id", "submission_id", "created_at"],
    )
    op.create_index("ix_form_file_submission_id", "form_file", ["submission_id"])


def downgrade() -> None:
    # Dropping a table drops its indexes, constraints and policies with it.
    for table in reversed(_TABLES):
        op.execute(disable_workspace_table_rls(table))
        op.drop_table(table)
    op.drop_column("workflow", "expects_order")
