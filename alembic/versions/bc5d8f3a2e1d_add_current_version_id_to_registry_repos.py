"""Add current_version_id to registry repositories.

Revision ID: bc5d8f3a2e1d
Revises: 045d54f6247c
Create Date: 2025-01-16 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bc5d8f3a2e1d"
down_revision: str | None = "045d54f6247c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add current_version_id to registry_repository
    op.add_column(
        "registry_repository",
        sa.Column("current_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_registry_repository_current_version_id",
        "registry_repository",
        "registry_version",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add current_version_id to platform_registry_repository
    op.add_column(
        "platform_registry_repository",
        sa.Column("current_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_platform_registry_repository_current_version_id",
        "platform_registry_repository",
        "platform_registry_version",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill registry_repository with newest version
    op.execute(
        """
        UPDATE registry_repository rr
        SET current_version_id = (
            SELECT rv.id FROM registry_version rv
            WHERE rv.repository_id = rr.id
            ORDER BY rv.created_at DESC, rv.id DESC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM registry_version rv WHERE rv.repository_id = rr.id
        )
        """
    )

    # Backfill platform_registry_repository with newest version
    op.execute(
        """
        UPDATE platform_registry_repository prr
        SET current_version_id = (
            SELECT prv.id FROM platform_registry_version prv
            WHERE prv.repository_id = prr.id
            ORDER BY prv.created_at DESC, prv.id DESC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM platform_registry_version prv WHERE prv.repository_id = prr.id
        )
        """
    )


def downgrade() -> None:
    # Drop FK and column from platform_registry_repository
    op.drop_constraint(
        "fk_platform_registry_repository_current_version_id",
        "platform_registry_repository",
        type_="foreignkey",
    )
    op.drop_column("platform_registry_repository", "current_version_id")

    # Drop FK and column from registry_repository
    op.drop_constraint(
        "fk_registry_repository_current_version_id",
        "registry_repository",
        type_="foreignkey",
    )
    op.drop_column("registry_repository", "current_version_id")
