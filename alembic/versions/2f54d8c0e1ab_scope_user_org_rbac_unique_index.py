"""Scope user org-wide RBAC uniqueness by organization

Revision ID: 2f54d8c0e1ab
Revises: a3d7c9e8b4f2
Create Date: 2026-05-28 14:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f54d8c0e1ab"
down_revision: str | None = "a3d7c9e8b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX_NAME = "ix_user_role_assignment_user_org_unique"


def upgrade() -> None:
    op.drop_index(
        _INDEX_NAME,
        table_name="user_role_assignment",
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        _INDEX_NAME,
        "user_role_assignment",
        ["organization_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade is not supported because users can have org-wide role "
        "assignments in multiple organizations after this migration; restoring "
        "the previous user-only unique index could fail or require deleting "
        "assignments. Restore the database from a backup or snapshot before "
        "rolling the application back."
    )
