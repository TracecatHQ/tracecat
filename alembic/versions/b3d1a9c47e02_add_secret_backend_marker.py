"""add secret backend marker

Adds a ``backend`` column to all secret tables. It records which secrets
backend a row was created under: ``db`` (default, values Fernet-encrypted in
``encrypted_keys``) or an external backend such as ``vault``, in which case
the row is a value-less registration (name, key names, type, environment)
and values are resolved from the external backend at runtime.

Runtime dispatch is currently global (``TRACECAT__SECRETS_BACKEND``); the
marker is an integrity guard (the database backend refuses to serve
external-backend registrations as empty values) and lays the groundwork for
future per-secret backend dispatch.

Purely additive: existing rows are backfilled to ``db`` via the server
default, which matches their actual storage.

Revision ID: b3d1a9c47e02
Revises: e32940d12293
Create Date: 2026-07-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d1a9c47e02"
down_revision: str | None = "e32940d12293"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SECRET_TABLES = ("secret", "organization_secret", "platform_secret")


def upgrade() -> None:
    for table in _SECRET_TABLES:
        op.add_column(
            table,
            sa.Column(
                "backend", sa.String(length=50), nullable=False, server_default="db"
            ),
        )


def downgrade() -> None:
    for table in _SECRET_TABLES:
        op.drop_column(table, "backend")
