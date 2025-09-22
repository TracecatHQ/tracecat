"""Add Temporal search attributes

Revision ID: db3c91261770
Revises: 849db8d3b59d
Create Date: 2025-01-18 21:27:38.967454

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "db3c91261770"
down_revision: str | None = "849db8d3b59d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# We removed the search attributes from the Temporal cluster
# as we rely on the API service FastAPI lifespan to add them
def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
