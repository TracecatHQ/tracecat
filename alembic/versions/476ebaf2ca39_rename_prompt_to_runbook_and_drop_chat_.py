"""rename_prompt_to_runbook_and_drop_chat_id

Revision ID: 476ebaf2ca39
Revises: 6de4fe1a2745
Create Date: 2025-09-12 20:47:13.651198

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "476ebaf2ca39"
down_revision: str | None = "cbba80f89a32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the foreign key constraint first
    op.drop_constraint("prompt_chat_id_fkey", "prompt", type_="foreignkey")

    # Drop the chat_id column
    op.drop_column("prompt", "chat_id")

    # Rename the table from prompt to runbook
    op.rename_table("prompt", "runbook")

    # Update any indexes if they exist with the old table name
    op.execute("ALTER INDEX IF EXISTS ix_prompt_id RENAME TO ix_runbook_id")
    op.execute("ALTER INDEX IF EXISTS ix_prompt_owner_id RENAME TO ix_runbook_owner_id")

    # Rename the unique constraint for alias
    op.execute(
        "ALTER TABLE runbook RENAME CONSTRAINT uq_prompt_alias_owner_id TO uq_runbook_alias_owner_id"
    )


def downgrade() -> None:
    # Rename the unique constraint back
    op.execute(
        "ALTER TABLE runbook RENAME CONSTRAINT uq_runbook_alias_owner_id TO uq_prompt_alias_owner_id"
    )

    # Rename the table back from runbook to prompt
    op.rename_table("runbook", "prompt")

    # Restore indexes to original names
    op.execute("ALTER INDEX IF EXISTS ix_runbook_id RENAME TO ix_prompt_id")
    op.execute("ALTER INDEX IF EXISTS ix_runbook_owner_id RENAME TO ix_prompt_owner_id")

    # Re-add the chat_id column
    op.add_column("prompt", sa.Column("chat_id", sa.UUID(), nullable=True))

    # Re-add the foreign key constraint
    op.create_foreign_key(
        "prompt_chat_id_fkey", "prompt", "chat", ["chat_id"], ["id"], ondelete="CASCADE"
    )
