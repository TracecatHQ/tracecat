"""migrate_provider_ids_remove_suffixes

Revision ID: 4c1a69e5b34b
Revises: 89a8d57c3608
Create Date: 2025-07-04 21:11:28.940515

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c1a69e5b34b"
down_revision: str | None = "89a8d57c3608"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Migrate existing OAuth integrations to remove _ac/_cc suffixes from provider_ids.

    This migration uses a static mapping approach to update specific provider IDs:
    1. Updates provider_ids by removing _ac (Authorization Code) and _cc (Client Credentials) suffixes
    2. Ensures grant_type is correctly set based on the original suffix
    3. Uses explicit mappings for all known legacy provider IDs
    """
    # Get a reference to the oauth_integration table
    connection = op.get_bind()

    # Static mapping for provider IDs with _ac suffix (Authorization Code)
    ac_mappings = [
        ("microsoft_ac", "microsoft"),
        ("microsoft_teams_ac", "microsoft_teams"),
    ]

    for old_provider_id, new_provider_id in ac_mappings:
        connection.execute(
            sa.text("""
                UPDATE oauth_integration
                SET provider_id = :new_provider_id,
                    grant_type = 'AUTHORIZATION_CODE'
                WHERE provider_id = :old_provider_id
            """),
            {"old_provider_id": old_provider_id, "new_provider_id": new_provider_id},
        )

    # Static mapping for provider IDs with _cc suffix (Client Credentials)
    cc_mappings = [
        ("microsoft_cc", "microsoft"),
        ("microsoft_teams_cc", "microsoft_teams"),
    ]

    for old_provider_id, new_provider_id in cc_mappings:
        connection.execute(
            sa.text("""
                UPDATE oauth_integration
                SET provider_id = :new_provider_id,
                    grant_type = 'CLIENT_CREDENTIALS'
                WHERE provider_id = :old_provider_id
            """),
            {"old_provider_id": old_provider_id, "new_provider_id": new_provider_id},
        )

    # Log the changes for verification
    result = connection.execute(
        sa.text("""
            SELECT provider_id, grant_type, COUNT(*) as count
            FROM oauth_integration
            GROUP BY provider_id, grant_type
            ORDER BY provider_id, grant_type
        """)
    )

    print("Updated OAuth integrations:")
    for row in result:
        print(f"  {row.provider_id} ({row.grant_type}): {row.count} records")


def downgrade() -> None:
    """
    Downgrade migration - restore _ac/_cc suffixes to provider_ids.

    This will restore the original format where grant types were encoded in provider_id suffixes.
    Uses static mapping to reverse the changes from upgrade().
    """
    connection = op.get_bind()

    # Static mapping to restore _ac suffix (Authorization Code)
    ac_reverse_mappings = [
        ("microsoft", "microsoft_ac"),
        ("microsoft_teams", "microsoft_teams_ac"),
    ]

    for new_provider_id, old_provider_id in ac_reverse_mappings:
        connection.execute(
            sa.text("""
                UPDATE oauth_integration
                SET provider_id = :old_provider_id
                WHERE provider_id = :new_provider_id
                AND grant_type = 'AUTHORIZATION_CODE'
            """),
            {"old_provider_id": old_provider_id, "new_provider_id": new_provider_id},
        )

    # Static mapping to restore _cc suffix (Client Credentials)
    cc_reverse_mappings = [
        ("microsoft", "microsoft_cc"),
        ("microsoft_teams", "microsoft_teams_cc"),
    ]

    for new_provider_id, old_provider_id in cc_reverse_mappings:
        connection.execute(
            sa.text("""
                UPDATE oauth_integration
                SET provider_id = :old_provider_id
                WHERE provider_id = :new_provider_id
                AND grant_type = 'CLIENT_CREDENTIALS'
            """),
            {"old_provider_id": old_provider_id, "new_provider_id": new_provider_id},
        )

    print("Restored provider_id suffixes for OAuth integrations")
