"""migrate_custom_provider_ids_hyphen_to_underscore

Revision ID: b873c83b8e0a
Revises: c13c1c2f4d93
Create Date: 2025-11-26 13:40:46.077313

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b873c83b8e0a"
down_revision: str | None = "c13c1c2f4d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Migrate provider_ids from custom- prefix to custom_ prefix.

    Updates provider_ids in both oauth_integration and oauth_provider tables
    that start with 'custom-' to use 'custom_' instead (replacing hyphen with underscore).
    """
    connection = op.get_bind()

    # Update oauth_integration table
    connection.execute(
        sa.text("""
            UPDATE oauth_integration
            SET provider_id = REPLACE(provider_id, 'custom-', 'custom_')
            WHERE provider_id LIKE 'custom-%'
        """)
    )

    # Update oauth_provider table
    connection.execute(
        sa.text("""
            UPDATE oauth_provider
            SET provider_id = REPLACE(provider_id, 'custom-', 'custom_')
            WHERE provider_id LIKE 'custom-%'
        """)
    )


def downgrade() -> None:
    """
    Downgrade migration - restore custom- prefix from custom_ prefix.

    Reverses the upgrade by replacing 'custom_' with 'custom-' in provider_ids.
    """
    connection = op.get_bind()

    # Restore oauth_integration table
    connection.execute(
        sa.text("""
            UPDATE oauth_integration
            SET provider_id = REPLACE(provider_id, 'custom_', 'custom-')
            WHERE provider_id LIKE 'custom_%'
        """)
    )

    # Restore oauth_provider table
    connection.execute(
        sa.text("""
            UPDATE oauth_provider
            SET provider_id = REPLACE(provider_id, 'custom_', 'custom-')
            WHERE provider_id LIKE 'custom_%'
        """)
    )
