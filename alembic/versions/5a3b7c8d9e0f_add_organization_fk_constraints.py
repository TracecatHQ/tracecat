"""add_organization_fk_constraints

Revision ID: 5a3b7c8d9e0f
Revises: 4ef4d2e1d57b
Create Date: 2026-01-27 18:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a3b7c8d9e0f"
down_revision: str | None = "4ef4d2e1d57b"
branch_labels: str | None = None
depends_on: str | None = None

# Tables that inherit from OrganizationModel and need FK constraints added
# These use RESTRICT to prevent accidental data loss
FK_TABLES_RESTRICT = [
    "organization_secret",
    "registry_repository",
    "registry_action",
    "registry_version",
    "registry_index",
]

# Tables that should CASCADE on org delete (settings are meaningless without org)
FK_TABLES_CASCADE = [
    "organization_settings",
]


def upgrade() -> None:
    """Add foreign key constraints to organization_id columns.

    Uses NOT VALID to avoid full table scan, then VALIDATE CONSTRAINT
    to check existing data without holding locks.
    """
    connection = op.get_bind()

    # Add FK constraints with RESTRICT (default, prevents accidental deletion)
    for table in FK_TABLES_RESTRICT:
        # Check if table exists
        table_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :table_name)"
            ),
            {"table_name": table},
        ).scalar()

        if not table_exists:
            continue

        constraint_name = f"fk_{table}_organization_id"

        # Check if constraint already exists
        constraint_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.table_constraints "
                "WHERE constraint_name = :constraint_name)"
            ),
            {"constraint_name": constraint_name},
        ).scalar()

        if constraint_exists:
            continue

        # Add FK constraint as NOT VALID (fast, no full table scan)
        connection.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                f"FOREIGN KEY (organization_id) REFERENCES organization(id) "
                f"ON DELETE RESTRICT NOT VALID"
            )
        )

        # Validate the constraint (checks existing data, but doesn't hold locks)
        connection.execute(
            sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint_name}")
        )

    # Add FK constraints with CASCADE (for tables where deletion should cascade)
    for table in FK_TABLES_CASCADE:
        table_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :table_name)"
            ),
            {"table_name": table},
        ).scalar()

        if not table_exists:
            continue

        constraint_name = f"fk_{table}_organization_id"

        constraint_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.table_constraints "
                "WHERE constraint_name = :constraint_name)"
            ),
            {"constraint_name": constraint_name},
        ).scalar()

        if constraint_exists:
            continue

        connection.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
                f"FOREIGN KEY (organization_id) REFERENCES organization(id) "
                f"ON DELETE CASCADE NOT VALID"
            )
        )

        connection.execute(
            sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint_name}")
        )


def downgrade() -> None:
    """Remove the foreign key constraints."""
    connection = op.get_bind()

    all_tables = FK_TABLES_RESTRICT + FK_TABLES_CASCADE

    for table in all_tables:
        table_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :table_name)"
            ),
            {"table_name": table},
        ).scalar()

        if not table_exists:
            continue

        constraint_name = f"fk_{table}_organization_id"

        constraint_exists = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.table_constraints "
                "WHERE constraint_name = :constraint_name)"
            ),
            {"constraint_name": constraint_name},
        ).scalar()

        if constraint_exists:
            connection.execute(
                sa.text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint_name}")
            )
