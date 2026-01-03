"""Drop wheel columns from registry_version

Revision ID: 5b2c8e91f3a7
Revises: 4a9dff5ba0f2
Create Date: 2025-12-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b2c8e91f3a7"
down_revision: str | None = "4a9dff5ba0f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop wheel columns and make tarball_uri required.

    Tarball-based installation is now the only supported method.
    """
    # First, delete any registry versions that don't have a tarball_uri
    # These are invalid under the new schema
    op.execute("DELETE FROM registry_version WHERE tarball_uri IS NULL")

    # Drop wheel columns
    op.drop_column("registry_version", "wheel_uri")
    op.drop_column("registry_version", "wheelhouse_uri")

    # Make tarball_uri NOT NULL since it's now required
    op.alter_column(
        "registry_version",
        "tarball_uri",
        existing_type=sa.String(),
        nullable=False,
    )


def downgrade() -> None:
    """Restore wheel columns and make tarball_uri nullable."""
    # Make tarball_uri nullable again
    op.alter_column(
        "registry_version",
        "tarball_uri",
        existing_type=sa.String(),
        nullable=True,
    )

    # Restore wheel columns
    op.add_column(
        "registry_version",
        sa.Column(
            "wheelhouse_uri",
            sa.String(),
            nullable=True,
            comment="S3 URI prefix to the dependency wheelhouse directory",
        ),
    )
    op.add_column(
        "registry_version",
        sa.Column(
            "wheel_uri",
            sa.String(),
            nullable=True,
            comment="S3 URI to the package wheel file",
        ),
    )
