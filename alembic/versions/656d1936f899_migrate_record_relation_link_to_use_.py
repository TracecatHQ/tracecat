"""migrate_record_relation_link_to_use_relation_definition

Revision ID: 656d1936f899
Revises: c9a075384345
Create Date: 2025-08-23 03:06:26.451322

"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '656d1936f899'
down_revision: str | None = 'c9a075384345'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Data migration: Create RelationDefinition entries for existing relation fields
    # and update RecordRelationLink to use relation_definition_id
    op.execute(sa.text("""
        WITH relation_mappings AS (
            -- Create a mapping of field_id to new relation_definition_id
            INSERT INTO relation_definition (
                id, owner_id, source_entity_id, source_key,
                target_entity_id, display_name, description,
                relation_type, is_active, deactivated_at,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid() as id,
                f.entity_id as owner_id,  -- Using entity_id as owner for now
                f.entity_id as source_entity_id,
                f.field_key as source_key,
                f.target_entity_id,
                f.display_name,
                f.description,
                -- Map old field types to new relation types
                CASE
                    WHEN f.field_type = 'RELATION_ONE_TO_ONE' THEN 'one_to_one'
                    WHEN f.field_type = 'RELATION_ONE_TO_MANY' THEN 'one_to_many'
                    WHEN f.field_type = 'RELATION_MANY_TO_ONE' THEN 'many_to_one'
                    WHEN f.field_type = 'RELATION_MANY_TO_MANY' THEN 'many_to_many'
                END as relation_type,
                f.is_active,
                f.deactivated_at,
                f.created_at,
                f.updated_at
            FROM field_metadata f
            WHERE f.field_type IN (
                'RELATION_ONE_TO_ONE', 'RELATION_ONE_TO_MANY',
                'RELATION_MANY_TO_ONE', 'RELATION_MANY_TO_MANY'
            )
            AND f.target_entity_id IS NOT NULL
            RETURNING id, source_key, source_entity_id
        )
        -- Update record_relation_link with the new relation_definition_id
        UPDATE record_relation_link rrl
        SET relation_definition_id = rm.id
        FROM relation_mappings rm
        JOIN field_metadata fm ON fm.field_key = rm.source_key
            AND fm.entity_id = rm.source_entity_id
        WHERE rrl.source_field_id = fm.id;
    """))

    # Drop old indexes that use source_field_id
    op.drop_index('uq_record_relation_source_single', table_name='record_relation_link')
    op.drop_index('uq_record_relation_target_single', table_name='record_relation_link')
    op.drop_index('idx_record_relation_source', table_name='record_relation_link')
    op.drop_index('idx_record_relation_field_target', table_name='record_relation_link')
    op.drop_constraint('uq_record_relation_link_triple', 'record_relation_link', type_='unique')

    # Drop source_field_id foreign key and column
    op.drop_constraint('record_relation_link_source_field_id_fkey', 'record_relation_link', type_='foreignkey')
    op.drop_column('record_relation_link', 'source_field_id')

    # Make relation_definition_id NOT NULL now that data is migrated
    op.alter_column('record_relation_link', 'relation_definition_id',
                    existing_type=sqlmodel.sql.sqltypes.GUID(),
                    nullable=False)

    # Create new indexes using relation_definition_id
    op.create_unique_constraint(
        'uq_record_relation_link_triple',
        'record_relation_link',
        ['source_record_id', 'relation_definition_id', 'target_record_id']
    )

    op.create_index(
        'idx_record_relation_source',
        'record_relation_link',
        ['source_record_id', 'relation_definition_id']
    )

    op.create_index(
        'idx_record_relation_def_target',
        'record_relation_link',
        ['relation_definition_id', 'target_record_id']
    )

    # Create partial unique indexes for cardinality enforcement
    op.create_index(
        'uq_record_relation_source_single',
        'record_relation_link',
        ['source_record_id', 'relation_definition_id'],
        unique=True,
        postgresql_where=sa.text('source_limit_one = true')
    )

    op.create_index(
        'uq_record_relation_target_single',
        'record_relation_link',
        ['target_record_id', 'relation_definition_id'],
        unique=True,
        postgresql_where=sa.text('target_limit_one = true')
    )

    # Clean up FieldMetadata: drop relation-specific columns
    op.drop_column('field_metadata', 'relation_kind')
    op.drop_column('field_metadata', 'target_entity_id')

    # Drop backref_field_id if it exists (from previous migration)
    op.drop_constraint('fk_field_metadata_backref_field_id', 'field_metadata', type_='foreignkey')
    op.drop_column('field_metadata', 'backref_field_id')


def downgrade() -> None:
    # Re-add columns to field_metadata
    op.add_column('field_metadata',
        sa.Column('relation_kind', sa.String(length=20), nullable=True))
    op.add_column('field_metadata',
        sa.Column('target_entity_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('field_metadata',
        sa.Column('backref_field_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))

    op.create_foreign_key(
        'fk_field_metadata_backref_field_id',
        'field_metadata',
        'field_metadata',
        ['backref_field_id'],
        ['id'],
        ondelete='SET NULL'
    )

    op.create_foreign_key(
        'field_metadata_target_entity_id_fkey',
        'field_metadata',
        'entity',
        ['target_entity_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Re-add source_field_id to record_relation_link
    op.add_column('record_relation_link',
        sa.Column('source_field_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))

    # Restore data from relation_definition back to field_metadata
    op.execute(sa.text("""
        -- Restore field_metadata relation fields from relation_definition
        UPDATE field_metadata f
        SET
            target_entity_id = rd.target_entity_id,
            relation_kind = CASE
                WHEN rd.relation_type = 'one_to_one' THEN 'ONE_TO_ONE'
                WHEN rd.relation_type = 'one_to_many' THEN 'ONE_TO_MANY'
                WHEN rd.relation_type = 'many_to_one' THEN 'MANY_TO_ONE'
                WHEN rd.relation_type = 'many_to_many' THEN 'MANY_TO_MANY'
            END
        FROM relation_definition rd
        WHERE f.field_key = rd.source_key
        AND f.entity_id = rd.source_entity_id
        AND f.field_type IN (
            'RELATION_ONE_TO_ONE', 'RELATION_ONE_TO_MANY',
            'RELATION_MANY_TO_ONE', 'RELATION_MANY_TO_MANY'
        );

        -- Restore source_field_id in record_relation_link
        UPDATE record_relation_link rrl
        SET source_field_id = f.id
        FROM field_metadata f
        JOIN relation_definition rd ON rd.source_key = f.field_key
            AND rd.source_entity_id = f.entity_id
        WHERE rrl.relation_definition_id = rd.id;
    """))

    # Drop new indexes
    op.drop_index('uq_record_relation_target_single', table_name='record_relation_link')
    op.drop_index('uq_record_relation_source_single', table_name='record_relation_link')
    op.drop_index('idx_record_relation_def_target', table_name='record_relation_link')
    op.drop_index('idx_record_relation_source', table_name='record_relation_link')
    op.drop_constraint('uq_record_relation_link_triple', 'record_relation_link', type_='unique')

    # Make source_field_id NOT NULL
    op.alter_column('record_relation_link', 'source_field_id',
                    existing_type=sqlmodel.sql.sqltypes.GUID(),
                    nullable=False)

    # Add back foreign key for source_field_id
    op.create_foreign_key(
        'recordrelationlink_source_field_id_fkey',
        'record_relation_link',
        'field_metadata',
        ['source_field_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Restore old indexes
    op.create_unique_constraint(
        'uq_record_relation_link_triple',
        'record_relation_link',
        ['source_record_id', 'source_field_id', 'target_record_id']
    )

    op.create_index(
        'idx_record_relation_source',
        'record_relation_link',
        ['source_record_id', 'source_field_id']
    )

    op.create_index(
        'idx_record_relation_field_target',
        'record_relation_link',
        ['source_field_id', 'target_record_id']
    )

    op.create_index(
        'uq_record_relation_source_single',
        'record_relation_link',
        ['source_record_id', 'source_field_id'],
        unique=True,
        postgresql_where=sa.text('source_limit_one = true')
    )

    op.create_index(
        'uq_record_relation_target_single',
        'record_relation_link',
        ['target_record_id', 'source_field_id'],
        unique=True,
        postgresql_where=sa.text('target_limit_one = true')
    )

    # Drop relation_definition_id
    op.alter_column('record_relation_link', 'relation_definition_id',
                    existing_type=sqlmodel.sql.sqltypes.GUID(),
                    nullable=True)
