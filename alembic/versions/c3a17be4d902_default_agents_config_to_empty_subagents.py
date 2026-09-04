"""Default agents config to empty subagents

Revision ID: c3a17be4d902
Revises: 44d7e75b6f4c
Create Date: 2026-09-04 12:00:00.000000

This is the expand half of an expand/contract pair. The legacy ``enabled`` key
is deliberately left on rows written before this release: migrations run ahead
of the application rollout, and the old app version reads a missing ``enabled``
as ``False``, which trips its ``subagents require enabled=true`` validator and
silently drops Agent-tool access. The new app strips the key on read. A later
contract migration removes it from stored rows once the old replicas are gone.

Only the column's server default changes here, which is safe for both app
versions because each sets ``agents`` explicitly on insert.
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


def downgrade() -> None:
    # Restore the previous server default. Stored rows are untouched by
    # upgrade(), so there is nothing to restore beyond the default.
    for table in _AGENTS_TABLES:
        op.alter_column(
            table,
            "agents",
            existing_type=_AGENTS_TYPE,
            existing_nullable=False,
            server_default=_LEGACY_DISABLED_SQL,
        )
