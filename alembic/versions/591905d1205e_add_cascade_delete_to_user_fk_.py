"""add_cascade_delete_to_user_fk_constraints

Revision ID: 591905d1205e
Revises: 0fd1f09cd98b
Create Date: 2025-12-09 22:53:04.405505

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "591905d1205e"
down_revision: str | None = "0fd1f09cd98b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE access_token
            DROP CONSTRAINT fk_access_token_user_id_user,
            ADD CONSTRAINT fk_access_token_user_id_user
                FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE oauth_account
            DROP CONSTRAINT fk_oauth_account_user_id_user,
            ADD CONSTRAINT fk_oauth_account_user_id_user
                FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE oauth_account
            DROP CONSTRAINT fk_oauth_account_user_id_user,
            ADD CONSTRAINT fk_oauth_account_user_id_user
                FOREIGN KEY (user_id) REFERENCES "user"(id)
    """)
    op.execute("""
        ALTER TABLE access_token
            DROP CONSTRAINT fk_access_token_user_id_user,
            ADD CONSTRAINT fk_access_token_user_id_user
                FOREIGN KEY (user_id) REFERENCES "user"(id)
    """)
