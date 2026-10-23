"""Enforce case-insensitive user email uniqueness

Revision ID: 14588b612098
Revises: b7d41c9e2a6f
Create Date: 2026-10-02 14:46:06.036544

Email lookups in fastapi-users are case-insensitive, but the ``ix_user_email``
unique index on ``user.email`` is case-sensitive. Creating a user whose email
differed only by case from an existing account produced a second row that
later crashed every case-insensitive lookup with ``MultipleResultsFound``.

Adds a functional unique index on ``lower(email)`` so the database enforces
what the lookups already assume. The index is additive: the previous app can
still run against the migrated schema, and a duplicate insert now fails
closed instead of persisting a corrupt duplicate.

If case-variant duplicates already exist, the upgrade reports only the number
of conflicting groups and makes no changes.

Operator recovery (outside the migration job): use an approved, access-controlled
database session with SELECT permission on the user table to inspect conflicts:

    SELECT id, email FROM "user"
    WHERE lower(email) IN (
        SELECT lower(email) FROM "user"
        GROUP BY lower(email) HAVING count(*) > 1
    )
    ORDER BY lower(email), id;

Keep query results in the restricted session, not deployment logs or tickets.
Confirm account ownership and dependencies before an authorized operator resolves
the conflicting accounts through the approved account-management process. Do not
blindly delete or merge rows. Re-run the query until it returns no rows, then retry
the migration. The migration never rewrites or deletes users.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "14588b612098"
down_revision: str | None = "b7d41c9e2a6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicate_group_count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM ("
                ' SELECT 1 FROM "user" GROUP BY lower(email) HAVING count(*) > 1'
                ") AS duplicate_groups"
            )
        )
        .scalar_one()
    )
    if duplicate_group_count:
        raise RuntimeError(
            f"Found {duplicate_group_count} case-insensitive duplicate user email "
            "groups; resolve these before continuing. See revision 14588b612098 "
            "for restricted operator inspection instructions."
        )
    op.create_index(
        "ix_user_email_lower", "user", [sa.text("lower(email)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_user_email_lower", table_name="user")
