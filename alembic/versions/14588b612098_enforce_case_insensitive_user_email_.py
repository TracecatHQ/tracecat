"""Enforce case-insensitive user email uniqueness

Revision ID: 14588b612098
Revises: 6d83f2a91c40
Create Date: 2026-10-02 14:46:06.036544

Email lookups in fastapi-users are case-insensitive, but the ``ix_user_email``
unique index on ``user.email`` is case-sensitive. Creating a user whose email
differed only by case from an existing account produced a second row that
later crashed every case-insensitive lookup with ``MultipleResultsFound``.

Adds a functional unique index on ``lower(email)`` so the database enforces
what the lookups already assume. The index is additive: the previous app can
still run against the migrated schema, and a duplicate insert now fails
closed instead of persisting a corrupt duplicate.

If case-variant duplicates already exist, the upgrade raises with the
conflicting emails and makes no changes; resolve them manually and re-run.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "14588b612098"
down_revision: str | None = "6d83f2a91c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT lower(email) AS email_key, array_agg(email) AS emails"
                ' FROM "user" GROUP BY email_key HAVING count(*) > 1'
            )
        )
        .all()
    )
    if duplicates:
        details = ", ".join(email for row in duplicates for email in sorted(row.emails))
        raise RuntimeError(
            "Found user emails that differ only by case; resolve these "
            f"duplicates before continuing: {details}"
        )
    op.create_index(
        "ix_user_email_lower", "user", [sa.text("lower(email)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_user_email_lower", table_name="user")
