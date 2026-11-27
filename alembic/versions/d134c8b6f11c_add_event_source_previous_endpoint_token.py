"""Add the previous endpoint token of an event source.

Revision ID: d134c8b6f11c
Revises: 9620041994d1
Create Date: 2026-11-28 10:00:00.000000

Expand only. Adds two nullable columns to ``event_source`` for endpoint URL
rotation: ``previous_endpoint_token`` (unique, like ``endpoint_token``) and
``previous_endpoint_token_expires_at``. Existing rows keep both null, so the
previous release reads and writes the table as before; it ignores the
columns, so an old URL kept by a rotation is refused by that release.

The downgrade drops both columns. Any rotation's overlap ends at once; current
tokens are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d134c8b6f11c"
down_revision: str | None = "9620041994d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE = "uq_event_source_previous_endpoint_token"


def upgrade() -> None:
    op.add_column(
        "event_source",
        sa.Column("previous_endpoint_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "event_source",
        sa.Column(
            "previous_endpoint_token_expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(_UNIQUE, "event_source", ["previous_endpoint_token"])


def downgrade() -> None:
    op.drop_constraint(_UNIQUE, "event_source", type_="unique")
    op.drop_column("event_source", "previous_endpoint_token_expires_at")
    op.drop_column("event_source", "previous_endpoint_token")
