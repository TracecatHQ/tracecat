"""add enabled model runtime config

Revision ID: 57cc1f153017
Revises: 2780a7872c8f
Create Date: 2026-03-10 16:18:20.087358

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "57cc1f153017"
down_revision: str | None = "2780a7872c8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_enabled_models",
        sa.Column(
            "enabled_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_enabled_models", "enabled_config")
