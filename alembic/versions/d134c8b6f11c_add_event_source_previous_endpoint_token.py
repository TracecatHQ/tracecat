"""Add the previous endpoint token of an event source.

Revision ID: d134c8b6f11c
Revises: 9620041994d1
Create Date: 2026-11-28 10:00:00.000000

Expand only. Adds three nullable columns to ``event_source`` for endpoint URL
rotation: ``previous_endpoint_token`` (unique, like ``endpoint_token``),
``previous_endpoint_token_expires_at`` and ``endpoint_token_confirmed_at``
(when the current URL was first confirmed; the API shows the URL only while it
is null). Every existing source gets ``endpoint_token_confirmed_at`` set to
its last update: a pending source may have been confirmed, unsubscribed and
restored through SNS, so it cannot be told apart from one never confirmed, and
its URL may be live. Such a source shows a URL again after a rotation, which
keeps the old URL for the overlap. The previous release ignores the columns:
it shows URLs as before, and refuses an old URL kept by a rotation.

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
    op.execute("UPDATE event_source SET endpoint_token_confirmed_at = updated_at")


def downgrade() -> None:
    op.drop_constraint(_UNIQUE, "event_source", type_="unique")
    op.drop_column("event_source", "endpoint_token_confirmed_at")
    op.drop_column("event_source", "previous_endpoint_token_expires_at")
    op.drop_column("event_source", "previous_endpoint_token")
