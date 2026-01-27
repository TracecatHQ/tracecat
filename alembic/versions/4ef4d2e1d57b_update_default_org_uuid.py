"""update_default_org_uuid

Revision ID: 4ef4d2e1d57b
Revises: 49a5c7464ab7
Create Date: 2026-01-26 00:00:00.000000

"""

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ef4d2e1d57b"
down_revision: str | None = "49a5c7464ab7"
branch_labels: str | None = None
depends_on: str | None = None


# The default organization UUID that was previously hardcoded
DEFAULT_ORG_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Update organizations with UUID(0) to a new random UUID.

    This migration:
    1. Finds any organization with id = UUID(0)
    2. Generates a new random UUID
    3. Updates the organization and all FK references

    Note: This is a data migration, not a schema migration. It only affects
    organizations that were created with the hardcoded default UUID.
    """
    # Get a connection for raw SQL execution
    connection = op.get_bind()

    # Check if any organization has the default UUID
    # Use CAST() instead of :: to avoid conflicts with SQLAlchemy's :param syntax
    result = connection.execute(
        sa.text("SELECT id FROM organization WHERE id = CAST(:default_uuid AS uuid)"),
        {"default_uuid": DEFAULT_ORG_UUID},
    )
    row = result.fetchone()

    if row is None:
        # No organization with UUID(0) exists, nothing to do
        return

    # Generate a new random UUID for the organization
    new_uuid = str(uuid4())

    # List of tables with organization_id foreign key
    fk_tables = [
        # Direct FK to organization.id
        "organization_setting",
        "organization_membership",
        "organization_invitation",
        "organization_tier",
        "workspace",
        "ownership",
        # Tables inheriting from OrganizationModel
        "organization_secret",
        "registry_repository",
        "registry_action",
        "registry_version",
        "registry_index",
    ]

    # Temporarily disable FK constraint triggers on all affected tables
    # This allows us to update the PK and FKs without constraint violations
    for table in fk_tables:
        table_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :table_name)"
            ),
            {"table_name": table},
        ).scalar()
        if table_exists:
            connection.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER ALL"))

    connection.execute(sa.text("ALTER TABLE organization DISABLE TRIGGER ALL"))

    # Update the organization table first (parent)
    connection.execute(
        sa.text(
            "UPDATE organization SET id = CAST(:new_uuid AS uuid) "
            "WHERE id = CAST(:old_uuid AS uuid)"
        ),
        {"new_uuid": new_uuid, "old_uuid": DEFAULT_ORG_UUID},
    )

    # Update all FK references in child tables
    for table in fk_tables:
        table_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :table_name)"
            ),
            {"table_name": table},
        ).scalar()

        if table_exists:
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET organization_id = CAST(:new_uuid AS uuid) "
                    f"WHERE organization_id = CAST(:old_uuid AS uuid)"
                ),
                {"new_uuid": new_uuid, "old_uuid": DEFAULT_ORG_UUID},
            )

    # Re-enable FK constraint triggers
    connection.execute(sa.text("ALTER TABLE organization ENABLE TRIGGER ALL"))
    for table in fk_tables:
        table_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :table_name)"
            ),
            {"table_name": table},
        ).scalar()
        if table_exists:
            connection.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER ALL"))


def downgrade() -> None:
    """This migration cannot be safely reversed.

    Reverting would require knowing which organization was originally UUID(0),
    and there's no reliable way to determine that after the upgrade.
    Additionally, reverting to UUID(0) would reintroduce the hardcoded default
    that this migration was intended to remove.
    """
    # Intentionally empty - this is a one-way migration
    pass
