"""rename_meta_to_provider_config_in_oauth_integration

Revision ID: e4068dcd2bef
Revises: a1718b08194f
Create Date: 2025-06-19 11:46:25.804935

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4068dcd2bef'
down_revision: Union[str, None] = 'a1718b08194f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename meta column to provider_config in oauth_integration table
    op.alter_column('oauth_integration', 'meta', new_column_name='provider_config')


def downgrade() -> None:
    # Rename provider_config column back to meta in oauth_integration table
    op.alter_column('oauth_integration', 'provider_config', new_column_name='meta')
