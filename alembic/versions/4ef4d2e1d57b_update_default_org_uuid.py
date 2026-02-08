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


def _get_fk_constraints(
    connection: sa.Connection,
) -> list[tuple[str, str, str, str, str, str, str]]:
    """Query all single-column FK constraints referencing organization(id).

    Returns list of (constraint_name, table_name, column_name,
    ref_table, ref_column, on_delete_action, on_update_action).

    Only single-column FKs are supported. Composite FKs are excluded.
    """
    rows = connection.execute(
        sa.text("""
            SELECT
                c.conname AS constraint_name,
                c.conrelid::regclass::text AS table_name,
                a.attname AS column_name,
                c.confrelid::regclass::text AS ref_table,
                af.attname AS ref_column,
                CASE c.confdeltype
                    WHEN 'a' THEN 'NO ACTION'
                    WHEN 'r' THEN 'RESTRICT'
                    WHEN 'c' THEN 'CASCADE'
                    WHEN 'n' THEN 'SET NULL'
                    WHEN 'd' THEN 'SET DEFAULT'
                END AS on_delete,
                CASE c.confupdtype
                    WHEN 'a' THEN 'NO ACTION'
                    WHEN 'r' THEN 'RESTRICT'
                    WHEN 'c' THEN 'CASCADE'
                    WHEN 'n' THEN 'SET NULL'
                    WHEN 'd' THEN 'SET DEFAULT'
                END AS on_update
            FROM pg_constraint c
            JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            JOIN pg_attribute af
                ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1]
            WHERE c.confrelid = 'organization'::regclass
                AND c.contype = 'f'
                AND array_length(c.conkey, 1) = 1
        """)
    ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]


def upgrade() -> None:
    """Update organizations with UUID(0) to a new random UUID.

    This migration:
    1. Finds any organization with id = UUID(0)
    2. Generates a new random UUID
    3. Temporarily drops FK constraints referencing organization(id)
    4. Updates the organization PK and all FK references
    5. Re-creates the FK constraints

    Note: This is a data migration, not a schema migration. It only affects
    organizations that were created with the hardcoded default UUID.
    """
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

    # Dynamically discover all FK constraints referencing organization(id).
    # This avoids hardcoding table names and works on managed databases
    # (e.g. RDS, Cloud SQL) that don't grant superuser privileges needed
    # for DISABLE TRIGGER ALL.
    fk_constraints = _get_fk_constraints(connection)

    # Also find ALL tables with an organization_id column. Some tables may
    # have the column but no FK constraint yet (FK added in a later migration).
    # We need to update those too, otherwise the later FK migration would fail.
    all_org_id_tables = [
        r[0]
        for r in connection.execute(
            sa.text("""
                SELECT c.relname
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE a.attname = 'organization_id'
                    AND c.relkind = 'r'
                    AND n.nspname = 'public'
                    AND c.relname != 'organization'
            """)
        ).fetchall()
    ]

    # Drop all FK constraints referencing organization(id)
    for constraint_name, table_name, *_ in fk_constraints:
        connection.execute(
            sa.text(f"ALTER TABLE {table_name} DROP CONSTRAINT {constraint_name}")
        )

    # Disable user-defined triggers on all affected tables (e.g. immutability
    # guards). DISABLE TRIGGER USER only affects user triggers and requires
    # table ownership, not superuser — safe for managed databases.
    for table_name in all_org_id_tables:
        connection.execute(sa.text(f"ALTER TABLE {table_name} DISABLE TRIGGER USER"))
    connection.execute(sa.text("ALTER TABLE organization DISABLE TRIGGER USER"))

    # Update the organization PK
    connection.execute(
        sa.text(
            "UPDATE organization SET id = CAST(:new_uuid AS uuid) "
            "WHERE id = CAST(:old_uuid AS uuid)"
        ),
        {"new_uuid": new_uuid, "old_uuid": DEFAULT_ORG_UUID},
    )

    # Update organization_id in ALL tables that have the column
    for table_name in all_org_id_tables:
        connection.execute(
            sa.text(
                f"UPDATE {table_name} "
                f"SET organization_id = CAST(:new_uuid AS uuid) "
                f"WHERE organization_id = CAST(:old_uuid AS uuid)"
            ),
            {"new_uuid": new_uuid, "old_uuid": DEFAULT_ORG_UUID},
        )

    # Re-enable user-defined triggers
    connection.execute(sa.text("ALTER TABLE organization ENABLE TRIGGER USER"))
    for table_name in all_org_id_tables:
        connection.execute(sa.text(f"ALTER TABLE {table_name} ENABLE TRIGGER USER"))

    # Re-create all FK constraints with original ON DELETE/ON UPDATE actions
    for (
        constraint_name,
        table_name,
        column_name,
        ref_table,
        ref_column,
        on_delete,
        on_update,
    ) in fk_constraints:
        connection.execute(
            sa.text(
                f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
                f"FOREIGN KEY ({column_name}) REFERENCES {ref_table}({ref_column}) "
                f"ON DELETE {on_delete} ON UPDATE {on_update}"
            )
        )


def downgrade() -> None:
    """This migration cannot be safely reversed.

    Reverting would require knowing which organization was originally UUID(0),
    and there's no reliable way to determine that after the upgrade.
    Additionally, reverting to UUID(0) would reintroduce the hardcoded default
    that this migration was intended to remove.
    """
    # Intentionally empty - this is a one-way migration
    pass
