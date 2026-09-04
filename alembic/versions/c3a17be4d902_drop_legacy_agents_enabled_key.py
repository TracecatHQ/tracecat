"""Drop the legacy agents 'enabled' key

Revision ID: c3a17be4d902
Revises: 44d7e75b6f4c
Create Date: 2026-09-04 12:00:00.000000

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
_EMPTY_SUBAGENTS_SQL = sa.text("'{\"subagents\": []}'::jsonb")
_LEGACY_DISABLED_SQL = sa.text("'{\"enabled\": false}'::jsonb")


def upgrade() -> None:
    for table in _AGENTS_TABLES:
        op.alter_column(
            table,
            "agents",
            existing_type=_AGENTS_TYPE,
            existing_nullable=False,
            server_default=_EMPTY_SUBAGENTS_SQL,
        )
        op.execute(
            f"UPDATE {table} SET agents = agents - 'enabled' WHERE agents ? 'enabled'"
        )


def downgrade() -> None:
    # Restore the previous server default only. The 'enabled' key is no longer
    # read by any schema, so existing rows are left untouched.
    for table in _AGENTS_TABLES:
        op.alter_column(
            table,
            "agents",
            existing_type=_AGENTS_TYPE,
            existing_nullable=False,
            server_default=_LEGACY_DISABLED_SQL,
        )
