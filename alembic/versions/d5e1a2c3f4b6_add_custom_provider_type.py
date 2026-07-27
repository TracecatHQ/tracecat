"""add custom provider type

Adds an explicit ``type`` discriminator to ``agent_custom_provider`` so
discovery and validation dispatch on a stored value instead of inferring from
display name or base URL. The column is a plain string (mirroring
``agent_catalog.model_provider``), not a DB enum, so new types can ship
without an enum migration.

Additive and backfilled by the server default: existing providers read back as
``generic_openai_compatible``, which preserves today's ``GET {base_url}/models``
discovery behavior.

Revision ID: d5e1a2c3f4b6
Revises: c6a8d4f3b2e1
Create Date: 2026-07-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e1a2c3f4b6"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_custom_provider",
        sa.Column(
            "type",
            sa.String(length=120),
            nullable=False,
            server_default="generic_openai_compatible",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_custom_provider", "type")
