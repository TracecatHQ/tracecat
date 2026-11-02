"""Add an opaque agent backend identity independently of the harness.

Existing and old-version writes retain the built-in oss backend through the
server default. No harness values or native history are rewritten.
Revision ID: acbacbf8ef53
Revises: b4e8f2a6c1d9
"""

import sqlalchemy as sa

from alembic import op

revision = "acbacbf8ef53"
down_revision = "b4e8f2a6c1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column("backend_id", sa.String(50), server_default="oss", nullable=False),
    )


def downgrade() -> None:
    # Refuse to discard routing identity while sessions depend on another backend.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM agent_session WHERE backend_id <> 'oss')")
    ):
        raise RuntimeError(
            "Cannot remove backend identity while non-oss sessions exist"
        )
    op.drop_column("agent_session", "backend_id")
