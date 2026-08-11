"""add role to case comment agent invocation

Revision ID: a235e885c62e
Revises: 35fa9fc71258
Create Date: 2026-08-11 16:15:27.884396

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a235e885c62e"
down_revision: str | None = "35fa9fc71258"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_comment_agent_invocation",
        sa.Column("role", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Existing pending rows predate durable role capture and cannot be replayed
    # without broadening the original caller's permissions. Fail only those rows;
    # running workflows already carry their real role in Temporal history.
    op.execute(
        sa.text(
            """
            UPDATE case_comment_agent_invocation AS invocation
            SET role = jsonb_build_object(
                    'type', 'service',
                    'workspace_id', invocation.workspace_id::text,
                    'bound_workspace_id', NULL,
                    'organization_id', workspace.organization_id::text,
                    'user_id', NULL,
                    'service_account_id', NULL,
                    'service_id', 'tracecat-api',
                    'is_platform_superuser', false,
                    'scopes', jsonb_build_array()
                ),
                status = CASE
                    WHEN invocation.status = 'pending' THEN 'failed'
                    ELSE invocation.status
                END,
                error = CASE
                    WHEN invocation.status = 'pending' THEN
                        'Invocation predates durable workflow delivery'
                    ELSE invocation.error
                END
            FROM workspace
            WHERE workspace.id = invocation.workspace_id
            """
        )
    )
    op.alter_column(
        "case_comment_agent_invocation",
        "role",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("case_comment_agent_invocation", "role")
