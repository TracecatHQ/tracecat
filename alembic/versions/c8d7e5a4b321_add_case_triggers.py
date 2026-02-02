"""add case triggers

Revision ID: c8d7e5a4b321
Revises: 663883860695
Create Date: 2026-02-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d7e5a4b321"
down_revision: str | None = "663883860695"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_trigger",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "event_types",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "tag_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint("workflow_id"),
    )
    op.create_index("ix_case_trigger_id", "case_trigger", ["id"], unique=False)

    op.execute(
        """
        INSERT INTO case_trigger
            (id, workspace_id, workflow_id, status, event_types, tag_filters, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            workspace_id,
            id,
            'offline',
            '[]'::jsonb,
            '[]'::jsonb,
            now(),
            now()
        FROM workflow;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_case_trigger_id", table_name="case_trigger")
    op.drop_table("case_trigger")
