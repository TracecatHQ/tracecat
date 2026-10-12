"""Add form, form_submission and form_event tables.

Revision ID: b8d2f6a31c45
Revises: a7c1e5f20b34
Create Date: 2026-10-12 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "b8d2f6a31c45"
down_revision: str | None = "a7c1e5f20b34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Child tables last, so downgrade can walk the tuple in reverse.
_TABLES = ("form", "form_submission", "form_event")


def upgrade() -> None:
    op.create_table(
        "form",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("success_message", sa.String(), nullable=True),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["workflow_id"],
            ["workflow.id"],
            name=op.f("fk_form_workflow_id_workflow"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_form_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_form")),
        sa.UniqueConstraint("workflow_id", name=op.f("uq_form_workflow_id")),
    )
    op.create_index(op.f("ix_form_id"), "form", ["id"], unique=True)
    op.create_index(
        "ix_form_workspace_status", "form", ["workspace_id", "status"], unique=False
    )
    op.create_table(
        "form_submission",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("form_id", sa.UUID(), nullable=False),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("submitted_by_service_account_id", sa.UUID(), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("wf_exec_id", sa.String(), nullable=False),
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
            name=op.f("fk_form_submission_form_id_form"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_service_account_id"],
            ["service_account.id"],
            name=op.f(
                "fk_form_submission_submitted_by_service_account_id_service_account"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["user.id"],
            name=op.f("fk_form_submission_submitted_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_form_submission_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_form_submission")),
    )
    op.create_index(
        "ix_form_submission_form_created",
        "form_submission",
        ["workspace_id", "form_id", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_form_submission_id"), "form_submission", ["id"], unique=True
    )
    op.create_index(
        "ix_form_submission_user_created",
        "form_submission",
        ["submitted_by_user_id", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_table(
        "form_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("form_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("service_account_id", sa.UUID(), nullable=True),
        sa.Column("submission_id", sa.UUID(), nullable=True),
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
            name=op.f("fk_form_event_form_id_form"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_account.id"],
            name=op.f("fk_form_event_service_account_id_service_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["form_submission.id"],
            name=op.f("fk_form_event_submission_id_form_submission"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_form_event_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_form_event_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_form_event")),
    )
    op.create_index(
        "ix_form_event_form_created",
        "form_event",
        ["workspace_id", "form_id", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_index(op.f("ix_form_event_id"), "form_event", ["id"], unique=True)
    op.create_index(
        "ix_form_event_view_dedupe",
        "form_event",
        ["workspace_id", "form_id", "type", "user_id", "created_at"],
        unique=False,
    )
    for table in _TABLES:
        op.execute(enable_workspace_table_rls(table))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(disable_workspace_table_rls(table))
    op.drop_index("ix_form_event_view_dedupe", table_name="form_event")
    op.drop_index(op.f("ix_form_event_id"), table_name="form_event")
    op.drop_index("ix_form_event_form_created", table_name="form_event")
    op.drop_table("form_event")
    op.drop_index("ix_form_submission_user_created", table_name="form_submission")
    op.drop_index(op.f("ix_form_submission_id"), table_name="form_submission")
    op.drop_index("ix_form_submission_form_created", table_name="form_submission")
    op.drop_table("form_submission")
    op.drop_index("ix_form_workspace_status", table_name="form")
    op.drop_index(op.f("ix_form_id"), table_name="form")
    op.drop_table("form")
