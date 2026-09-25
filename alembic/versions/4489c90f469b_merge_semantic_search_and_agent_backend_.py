"""Merge semantic-search and agent-backend migration heads.

Revision ID: 4489c90f469b
Revises: 8c0e18190001, acbacbf8ef53

Both branches are additive and touch independent objects. This merge changes
only Alembic history; it performs no schema or data operations in either direction.
"""

revision = "4489c90f469b"
down_revision = ("8c0e18190001", "acbacbf8ef53")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
