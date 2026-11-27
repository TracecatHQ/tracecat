"""Add the previous endpoint token of an event source.

Revision ID: d134c8b6f11c
Revises: 9620041994d1
Create Date: 2026-11-28 10:00:00.000000

Expand only. Adds three nullable columns to ``event_source`` for endpoint URL
rotation: ``previous_endpoint_token`` (unique, like ``endpoint_token``),
``previous_endpoint_token_expires_at`` and ``endpoint_token_confirmed_at``
(when the current URL was first confirmed; the API shows the URL only while it
is null). Sources confirmed now get ``endpoint_token_confirmed_at`` set to
their last update, so their live URLs stay hidden. The previous release
ignores the columns: it shows URLs as before, and refuses an old URL kept by a
rotation.

The downgrade drops the three columns. Any rotation's overlap ends at once;
current tokens are untouched.
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
    op.add_column(
        "event_source",
        sa.Column(
            "endpoint_token_confirmed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(_UNIQUE, "event_source", ["previous_endpoint_token"])
    op.execute(
        "UPDATE event_source SET endpoint_token_confirmed_at = updated_at"
        " WHERE subscription_status = 'confirmed'"
    )


def downgrade() -> None:
    op.drop_constraint(_UNIQUE, "event_source", type_="unique")
    op.drop_column("event_source", "endpoint_token_confirmed_at")
    op.drop_column("event_source", "previous_endpoint_token_expires_at")
    op.drop_column("event_source", "previous_endpoint_token")
