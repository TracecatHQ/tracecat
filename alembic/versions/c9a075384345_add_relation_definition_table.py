"""add_relation_definition_table

Revision ID: c9a075384345
Revises: 7d2f1aa9c3a4
Create Date: 2025-08-23 03:06:04.162147

"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9a075384345'
down_revision: str | None = '7d2f1aa9c3a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create relation_definition table
    op.create_table(
        'relation_definition',
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('owner_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),

        # Source entity and key
        sa.Column('source_entity_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('source_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),

        # Target entity
        sa.Column('target_entity_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),

        # Relation metadata
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('relation_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),

        # Lifecycle management
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('deactivated_at', sa.TIMESTAMP(timezone=True), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_entity_id'], ['entity.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_entity_id'], ['entity.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('source_entity_id', 'source_key', name='uq_relation_source_key'),
    )

    # Create indexes
    op.create_index('idx_relation_source', 'relation_definition', ['source_entity_id'])
    op.create_index('idx_relation_target', 'relation_definition', ['target_entity_id'])
    op.create_index('idx_relation_active', 'relation_definition', ['is_active'])
    op.create_index('idx_relation_owner', 'relation_definition', ['owner_id'])

    # Add relation_definition_id to record_relation_link
    op.add_column(
        'record_relation_link',
        sa.Column('relation_definition_id', sqlmodel.sql.sqltypes.GUID(), nullable=True)
    )

    # Add foreign key for relation_definition_id
    op.create_foreign_key(
        'fk_record_relation_link_relation_definition_id',
        'record_relation_link',
        'relation_definition',
        ['relation_definition_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Create index on relation_definition_id
    op.create_index(
        'idx_record_relation_def',
        'record_relation_link',
        ['relation_definition_id']
    )


def downgrade() -> None:
    # Drop index and foreign key from record_relation_link
    op.drop_index('idx_record_relation_def', table_name='record_relation_link')
    op.drop_constraint('fk_record_relation_link_relation_definition_id', 'record_relation_link', type_='foreignkey')
    op.drop_column('record_relation_link', 'relation_definition_id')

    # Drop indexes
    op.drop_index('idx_relation_owner', table_name='relation_definition')
    op.drop_index('idx_relation_active', table_name='relation_definition')
    op.drop_index('idx_relation_target', table_name='relation_definition')
    op.drop_index('idx_relation_source', table_name='relation_definition')

    # Drop table
    op.drop_table('relation_definition')
