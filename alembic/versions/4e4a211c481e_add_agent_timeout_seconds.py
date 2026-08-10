"""Add configurable agent timeouts.

Revision ID: 4e4a211c481e
Revises: c20c04c7d2a9
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4e4a211c481e"
down_revision: str | None = "c20c04c7d2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows retain null as an explicit "inherit the deployment timeout"
    # sentinel. New presets receive the product default at the API boundary.
    op.add_column(
        "agent_preset",
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_agent_preset_timeout_seconds_range"),
        "agent_preset",
        "timeout_seconds >= 5 AND timeout_seconds <= 3600",
    )
    op.add_column(
        "agent_preset_version",
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_agent_preset_version_timeout_seconds_range"),
        "agent_preset_version",
        "timeout_seconds >= 5 AND timeout_seconds <= 3600",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_preset_version_timeout_seconds_range"),
        "agent_preset_version",
        type_="check",
    )
    op.drop_column("agent_preset_version", "timeout_seconds")
    op.drop_constraint(
        op.f("ck_agent_preset_timeout_seconds_range"),
        "agent_preset",
        type_="check",
    )
    op.drop_column("agent_preset", "timeout_seconds")
