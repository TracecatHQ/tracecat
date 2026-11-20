"""Add tool call and agent turn concurrency limits to tiers

Revision ID: 0f1e1d98e9c0
Revises: 14588b612098
Create Date: 2026-11-20 12:00:00.000000

Adds nullable ``max_concurrent_tool_calls`` and ``max_concurrent_agent_turns``
to ``tier`` and ``organization_tier``. On a tier, null means unlimited. On an
organization override, null means inherit the tier value. Existing rows stay
null, so every organization stays unlimited.

The change is additive: the previous app version ignores the new columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f1e1d98e9c0"
down_revision: str | None = "14588b612098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("tier", "organization_tier")
_COLUMNS = ("max_concurrent_tool_calls", "max_concurrent_agent_turns")


def upgrade() -> None:
    for table in _TABLES:
        for column in _COLUMNS:
            op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        for column in _COLUMNS:
            op.drop_column(table, column)
