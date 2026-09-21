"""Allow saving table selections before an embedding provider is available.

Revision ID: 8c0e18190001
Revises: 391f391da70b, bc3124ad3437

Existing configured collections are unchanged. A NULL configuration never passes
current-version eligibility or worker claims, including in the previous writer.
Enable semantic search only after every source writer is upgraded.
"""

import sqlalchemy as sa

from alembic import op

revision = "8c0e18190001"
down_revision = ("391f391da70b", "bc3124ad3437")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "search_collection",
        "config_version",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    # Fail rather than discard saved selections that have no provider binding.
    unconfigured_selection = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM search_collection WHERE config_version IS NULL LIMIT 1"
            )
        )
        .first()
    )
    if unconfigured_selection is not None:
        raise NotImplementedError(
            "Cannot downgrade while providerless search selections exist: "
            "the previous schema requires a provider binding. Restore the database "
            "from a backup or snapshot before rolling the application back, or "
            "explicitly remove the incompatible selections before retrying. "
            "No selections have been removed by this migration."
        )
    op.alter_column(
        "search_collection",
        "config_version",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
