"""Default agents config to empty subagents

Revision ID: c3a17be4d902
Revises: 44d7e75b6f4c
Create Date: 2026-09-04 12:00:00.000000

The deprecated ``enabled`` key remains readable by older app versions during
rolling deploys and application rollback. New app writes normalize it to True;
existing values are preserved here because the migration runs before rollout.

Only defaults for new rows change. Existing configurations and dependency links,
including references to deleted agents and archived Skills in saved versions,
remain untouched so application rollback retains the previous data. Permanent
cleanup belongs in a later contract release after the rollback window closes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a17be4d902"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AGENTS_TABLES = ("agent_preset", "agent_preset_version")
_AGENTS_TYPE = postgresql.JSONB(astext_type=sa.Text())
_PREVIOUS_AGENTS_DEFAULT_SQL = sa.text("'{\"enabled\": false}'::jsonb")
_AGENTS_DEFAULT_SQL = sa.text('\'{"enabled": true, "subagents": []}\'::jsonb')


def upgrade() -> None:
    for table in _AGENTS_TABLES:
        op.alter_column(
            table,
            "agents",
            existing_type=_AGENTS_TYPE,
            existing_nullable=False,
            server_default=_AGENTS_DEFAULT_SQL,
        )


def downgrade() -> None:
    for table in _AGENTS_TABLES:
        op.alter_column(
            table,
            "agents",
            existing_type=_AGENTS_TYPE,
            existing_nullable=False,
            server_default=_PREVIOUS_AGENTS_DEFAULT_SQL,
        )
