"""redesign spm asset taxonomy

Revision ID: ba8d9e1c2f34
Revises: a46c2f1d9b87
Create Date: 2026-04-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ba8d9e1c2f34"
down_revision: str | None = "a46c2f1d9b87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _clear_spm_derived_data() -> None:
    op.execute("DELETE FROM spm_finding_decision")
    op.execute("DELETE FROM spm_enforcement_task")
    op.execute("DELETE FROM spm_finding")
    op.execute("DELETE FROM spm_asset_sighting")
    op.execute("DELETE FROM spm_asset")


def upgrade() -> None:
    _clear_spm_derived_data()

    op.drop_constraint("uq_spm_asset_org_identity", "spm_asset", type_="unique")
    op.drop_index("ix_spm_asset_org_harness_class_type", table_name="spm_asset")
    op.drop_column("spm_asset", "asset_class")
    op.add_column(
        "spm_asset",
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
    )
    op.add_column(
        "spm_asset",
        sa.Column("artifact_location", sa.String(length=1024), nullable=False),
    )
    op.create_unique_constraint(
        "uq_spm_asset_org_identity",
        "spm_asset",
        [
            "organization_id",
            "harness",
            "asset_type",
            "artifact_type",
            "artifact_location",
            "identity_key",
        ],
    )
    op.create_index(
        "ix_spm_asset_org_harness_type_artifact",
        "spm_asset",
        ["organization_id", "harness", "asset_type", "artifact_type"],
    )

    op.drop_column("spm_finding", "asset_class")
    op.add_column(
        "spm_finding",
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
    )
    op.add_column(
        "spm_finding",
        sa.Column("artifact_location", sa.String(length=1024), nullable=False),
    )


def downgrade() -> None:
    _clear_spm_derived_data()

    op.drop_column("spm_finding", "artifact_location")
    op.drop_column("spm_finding", "artifact_type")
    op.add_column(
        "spm_finding",
        sa.Column("asset_class", sa.String(length=64), nullable=False),
    )

    op.drop_constraint("uq_spm_asset_org_identity", "spm_asset", type_="unique")
    op.drop_index("ix_spm_asset_org_harness_type_artifact", table_name="spm_asset")
    op.drop_column("spm_asset", "artifact_location")
    op.drop_column("spm_asset", "artifact_type")
    op.add_column(
        "spm_asset",
        sa.Column("asset_class", sa.String(length=64), nullable=False),
    )
    op.create_unique_constraint(
        "uq_spm_asset_org_identity",
        "spm_asset",
        ["organization_id", "harness", "asset_class", "asset_type", "identity_key"],
    )
    op.create_index(
        "ix_spm_asset_org_harness_class_type",
        "spm_asset",
        ["organization_id", "harness", "asset_class", "asset_type"],
    )
