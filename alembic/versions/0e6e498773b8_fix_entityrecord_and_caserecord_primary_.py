"""Fix EntityRecord and CaseRecord primary keys and add sequences

Revision ID: 0e6e498773b8
Revises: eed5d1bd02c2
Create Date: 2025-08-28 23:46:51.117044

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e6e498773b8'
down_revision: str | None = 'eed5d1bd02c2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop existing composite primary key constraints
    op.drop_constraint('entity_record_pkey', 'entity_record', type_='primary')
    op.drop_constraint('case_record_pkey', 'case_record', type_='primary')

    # Create sequences for surrogate_id columns
    op.execute('CREATE SEQUENCE entity_record_surrogate_id_seq')
    op.execute('CREATE SEQUENCE case_record_surrogate_id_seq')

    # Set the surrogate_id columns to use the sequences and make them primary keys
    op.alter_column('entity_record', 'surrogate_id',
                    server_default=sa.text("nextval('entity_record_surrogate_id_seq')"),
                    nullable=False)
    op.alter_column('case_record', 'surrogate_id',
                    server_default=sa.text("nextval('case_record_surrogate_id_seq')"),
                    nullable=False)

    # Assign sequence ownership to the columns
    op.execute('ALTER SEQUENCE entity_record_surrogate_id_seq OWNED BY entity_record.surrogate_id')
    op.execute('ALTER SEQUENCE case_record_surrogate_id_seq OWNED BY case_record.surrogate_id')

    # Set the sequences to start from the max existing value + 1 (or 1 if table is empty)
    op.execute("""
        SELECT setval('entity_record_surrogate_id_seq',
                     COALESCE((SELECT MAX(surrogate_id) FROM entity_record), 0) + 1,
                     false)
    """)
    op.execute("""
        SELECT setval('case_record_surrogate_id_seq',
                     COALESCE((SELECT MAX(surrogate_id) FROM case_record), 0) + 1,
                     false)
    """)

    # Create single primary key constraints on surrogate_id only
    op.create_primary_key('entity_record_pkey', 'entity_record', ['surrogate_id'])
    op.create_primary_key('case_record_pkey', 'case_record', ['surrogate_id'])


def downgrade() -> None:
    # Drop single primary key constraints
    op.drop_constraint('entity_record_pkey', 'entity_record', type_='primary')
    op.drop_constraint('case_record_pkey', 'case_record', type_='primary')

    # Remove sequence defaults
    op.alter_column('entity_record', 'surrogate_id',
                    server_default=None,
                    nullable=False)
    op.alter_column('case_record', 'surrogate_id',
                    server_default=None,
                    nullable=False)

    # Drop sequences
    op.execute('DROP SEQUENCE entity_record_surrogate_id_seq')
    op.execute('DROP SEQUENCE case_record_surrogate_id_seq')

    # Recreate composite primary key constraints
    op.create_primary_key('entity_record_pkey', 'entity_record', ['surrogate_id', 'id'])
    op.create_primary_key('case_record_pkey', 'case_record', ['surrogate_id', 'id'])
