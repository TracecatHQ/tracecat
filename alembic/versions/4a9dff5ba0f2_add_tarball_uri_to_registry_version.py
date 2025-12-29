"""Add tarball_uri to registry_version

Revision ID: 4a9dff5ba0f2
Revises: de4ed9ddff3d
Create Date: 2025-12-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a9dff5ba0f2"
down_revision: str | None = "de4ed9ddff3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tarball_uri column and make wheel_uri nullable.

    - tarball_uri: S3 URI to a pre-built compressed tarball venv,
      which is an alternative to wheel installation for faster deployment.
    - wheel_uri: Now nullable since git registries only use tarballs.
    """
    # Add tarball_uri column
    op.add_column(
        "registry_version",
        sa.Column(
            "tarball_uri",
            sa.String(),
            nullable=True,
            comment="S3 URI to the compressed tarball venv (for git registries)",
        ),
    )

    # Make wheel_uri nullable (git registries don't have wheels, only tarballs)
    op.alter_column(
        "registry_version",
        "wheel_uri",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    """Remove tarball_uri column and make wheel_uri non-nullable."""
    # Delete rows with NULL wheel_uri before making column non-nullable
    # (These rows would be invalid under the pre-migration schema)
    op.execute("DELETE FROM registry_version WHERE wheel_uri IS NULL")

    # Make wheel_uri non-nullable again
    op.alter_column(
        "registry_version",
        "wheel_uri",
        existing_type=sa.String(),
        nullable=False,
    )

    # Remove tarball_uri column
    op.drop_column("registry_version", "tarball_uri")
