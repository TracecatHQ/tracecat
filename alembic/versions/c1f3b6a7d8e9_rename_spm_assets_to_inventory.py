"""rename spm assets to inventory

Revision ID: c1f3b6a7d8e9
Revises: ba8d9e1c2f34
Create Date: 2026-04-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_org_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "c1f3b6a7d8e9"
down_revision: str | None = "ba8d9e1c2f34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SOURCE_TYPE_TO_SNAKE = {
    "settings.json": "settings_json",
    "settings.local.json": "settings_local_json",
    ".claude.json": "claude_json",
    "hooks.json": "hooks_json",
    ".mcp.json": "mcp_json",
    "CLAUDE.md": "claude_md",
    "CLAUDE.local.md": "claude_local_md",
    "AGENTS.md": "agents_md",
    "skill-frontmatter": "skill_frontmatter",
    "agent-frontmatter": "agent_frontmatter",
    "plugin.json": "plugin_manifest",
    "directory": "directory",
}
SOURCE_TYPE_FROM_SNAKE = {value: key for key, value in SOURCE_TYPE_TO_SNAKE.items()}


def _source_type_case(mapping: dict[str, str]) -> str:
    whens = " ".join(
        f"WHEN '{old}' THEN '{new}'" for old, new in sorted(mapping.items())
    )
    return f"CASE source_type {whens} ELSE source_type END"


def _rename_index(old: str, new: str) -> None:
    op.execute(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    op.execute(disable_org_table_rls("spm_asset"))
    op.execute(disable_org_optional_workspace_table_rls("spm_asset_sighting"))

    op.rename_table("spm_asset", "spm_inventory_item")
    op.rename_table("spm_asset_sighting", "spm_inventory_observation")

    op.alter_column("spm_inventory_item", "asset_type", new_column_name="item_type")
    op.alter_column(
        "spm_inventory_item", "artifact_type", new_column_name="source_type"
    )
    op.alter_column(
        "spm_inventory_item",
        "artifact_location",
        new_column_name="source_location",
    )
    op.add_column(
        "spm_inventory_item",
        sa.Column("item_location", sa.String(length=1024), nullable=True),
    )
    op.execute(
        f"""
        UPDATE spm_inventory_item
        SET source_type = {_source_type_case(SOURCE_TYPE_TO_SNAKE)}
        """
    )
    op.execute(
        """
        UPDATE spm_inventory_item
        SET item_location = CASE
            WHEN item_type IN ('trusted_directory', 'additional_directory')
                THEN COALESCE(metadata ->> 'directory_path', identity_key)
            WHEN item_type = 'mcp_server'
                THEN COALESCE(metadata ->> 'mcp_identity_key', identity_key)
            ELSE source_location
        END
        """
    )
    op.alter_column("spm_inventory_item", "item_location", nullable=False)

    op.alter_column(
        "spm_inventory_observation", "asset_id", new_column_name="inventory_item_id"
    )

    op.alter_column("spm_finding", "asset_id", new_column_name="inventory_item_id")
    op.alter_column(
        "spm_finding",
        "asset_sighting_id",
        new_column_name="inventory_observation_id",
    )
    op.alter_column("spm_finding", "asset_type", new_column_name="item_type")
    op.alter_column("spm_finding", "artifact_type", new_column_name="source_type")
    op.alter_column(
        "spm_finding", "artifact_location", new_column_name="source_location"
    )
    op.add_column(
        "spm_finding",
        sa.Column("item_location", sa.String(length=1024), nullable=True),
    )
    op.execute(
        f"""
        UPDATE spm_finding
        SET source_type = {_source_type_case(SOURCE_TYPE_TO_SNAKE)}
        """
    )
    op.execute(
        """
        UPDATE spm_finding AS finding
        SET item_location = item.item_location
        FROM spm_inventory_item AS item
        WHERE item.id = finding.inventory_item_id
        """
    )
    op.execute(
        """
        UPDATE spm_finding
        SET item_location = source_location
        WHERE item_location IS NULL
        """
    )
    op.alter_column("spm_finding", "item_location", nullable=False)

    op.drop_constraint(
        "uq_spm_asset_org_identity", "spm_inventory_item", type_="unique"
    )
    op.create_unique_constraint(
        "uq_spm_inventory_item_org_identity",
        "spm_inventory_item",
        [
            "organization_id",
            "harness",
            "item_type",
            "source_type",
            "item_location",
            "source_location",
            "identity_key",
        ],
    )
    op.drop_index(
        "ix_spm_asset_org_harness_type_artifact",
        table_name="spm_inventory_item",
    )
    op.create_index(
        "ix_spm_inventory_item_org_harness_type_source",
        "spm_inventory_item",
        ["organization_id", "harness", "item_type", "source_type"],
    )
    _rename_index("ix_spm_asset_id", "ix_spm_inventory_item_id")
    _rename_index("ix_spm_asset_org_updated", "ix_spm_inventory_item_org_updated")
    _rename_index("ix_spm_asset_org_last_seen", "ix_spm_inventory_item_org_last_seen")

    op.drop_constraint(
        "uq_spm_asset_sighting_endpoint_asset",
        "spm_inventory_observation",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_spm_inventory_observation_endpoint_item",
        "spm_inventory_observation",
        ["organization_id", "endpoint_id", "inventory_item_id"],
    )
    _rename_index("ix_spm_asset_sighting_id", "ix_spm_inventory_observation_id")
    _rename_index(
        "ix_spm_asset_sighting_asset_id",
        "ix_spm_inventory_observation_inventory_item_id",
    )
    _rename_index(
        "ix_spm_asset_sighting_endpoint_id",
        "ix_spm_inventory_observation_endpoint_id",
    )
    _rename_index(
        "ix_spm_asset_sighting_workspace_id",
        "ix_spm_inventory_observation_workspace_id",
    )
    _rename_index(
        "ix_spm_asset_sighting_org_seen",
        "ix_spm_inventory_observation_org_seen",
    )
    _rename_index(
        "ix_spm_asset_sighting_org_workspace_seen",
        "ix_spm_inventory_observation_org_workspace_seen",
    )

    op.drop_constraint(
        "uq_spm_finding_endpoint_asset_control", "spm_finding", type_="unique"
    )
    op.create_unique_constraint(
        "uq_spm_finding_endpoint_item_control",
        "spm_finding",
        ["organization_id", "endpoint_id", "inventory_item_id", "control_id"],
    )
    _rename_index("ix_spm_finding_asset_id", "ix_spm_finding_inventory_item_id")
    _rename_index(
        "ix_spm_finding_asset_sighting_id",
        "ix_spm_finding_inventory_observation_id",
    )

    op.create_table(
        "spm_inventory_relationship",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.UUID(), nullable=False),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("from_inventory_item_id", sa.UUID(), nullable=False),
        sa.Column("to_inventory_item_id", sa.UUID(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "observed_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["spm_endpoint.id"],
            name=op.f("fk_spm_inventory_relationship_endpoint_id_spm_endpoint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_inventory_item_id"],
            ["spm_inventory_item.id"],
            name=op.f(
                "fk_spm_inventory_relationship_from_inventory_item_id_spm_inventory_item"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_inventory_relationship_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_inventory_item_id"],
            ["spm_inventory_item.id"],
            name=op.f(
                "fk_spm_inventory_relationship_to_inventory_item_id_spm_inventory_item"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_spm_inventory_relationship")
        ),
        sa.UniqueConstraint(
            "organization_id",
            "endpoint_id",
            "relationship_type",
            "from_inventory_item_id",
            "to_inventory_item_id",
            name="uq_spm_inventory_relationship_endpoint_items",
        ),
    )
    op.create_index(
        op.f("ix_spm_inventory_relationship_id"),
        "spm_inventory_relationship",
        ["id"],
        unique=True,
    )
    op.create_index(
        "ix_spm_inventory_relationship_org_endpoint",
        "spm_inventory_relationship",
        ["organization_id", "endpoint_id"],
    )
    op.create_index(
        "ix_spm_inventory_relationship_org_from",
        "spm_inventory_relationship",
        ["organization_id", "from_inventory_item_id"],
    )
    op.create_index(
        "ix_spm_inventory_relationship_org_to",
        "spm_inventory_relationship",
        ["organization_id", "to_inventory_item_id"],
    )

    op.execute(enable_org_table_rls("spm_inventory_item"))
    op.execute(enable_org_optional_workspace_table_rls("spm_inventory_observation"))
    op.execute(enable_org_table_rls("spm_inventory_relationship"))


def downgrade() -> None:
    op.execute(disable_org_table_rls("spm_inventory_relationship"))
    op.drop_index(
        "ix_spm_inventory_relationship_org_to",
        table_name="spm_inventory_relationship",
    )
    op.drop_index(
        "ix_spm_inventory_relationship_org_from",
        table_name="spm_inventory_relationship",
    )
    op.drop_index(
        "ix_spm_inventory_relationship_org_endpoint",
        table_name="spm_inventory_relationship",
    )
    op.drop_index(
        op.f("ix_spm_inventory_relationship_id"),
        table_name="spm_inventory_relationship",
    )
    op.drop_table("spm_inventory_relationship")

    op.execute(disable_org_table_rls("spm_inventory_item"))
    op.execute(disable_org_optional_workspace_table_rls("spm_inventory_observation"))

    op.drop_constraint(
        "uq_spm_finding_endpoint_item_control", "spm_finding", type_="unique"
    )
    op.create_unique_constraint(
        "uq_spm_finding_endpoint_asset_control",
        "spm_finding",
        ["organization_id", "endpoint_id", "inventory_item_id", "control_id"],
    )
    _rename_index("ix_spm_finding_inventory_item_id", "ix_spm_finding_asset_id")
    _rename_index(
        "ix_spm_finding_inventory_observation_id",
        "ix_spm_finding_asset_sighting_id",
    )

    op.drop_constraint(
        "uq_spm_inventory_observation_endpoint_item",
        "spm_inventory_observation",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_spm_asset_sighting_endpoint_asset",
        "spm_inventory_observation",
        ["organization_id", "endpoint_id", "inventory_item_id"],
    )
    _rename_index("ix_spm_inventory_observation_id", "ix_spm_asset_sighting_id")
    _rename_index(
        "ix_spm_inventory_observation_inventory_item_id",
        "ix_spm_asset_sighting_asset_id",
    )
    _rename_index(
        "ix_spm_inventory_observation_endpoint_id",
        "ix_spm_asset_sighting_endpoint_id",
    )
    _rename_index(
        "ix_spm_inventory_observation_workspace_id",
        "ix_spm_asset_sighting_workspace_id",
    )
    _rename_index(
        "ix_spm_inventory_observation_org_seen",
        "ix_spm_asset_sighting_org_seen",
    )
    _rename_index(
        "ix_spm_inventory_observation_org_workspace_seen",
        "ix_spm_asset_sighting_org_workspace_seen",
    )

    op.drop_constraint(
        "uq_spm_inventory_item_org_identity",
        "spm_inventory_item",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_spm_asset_org_identity",
        "spm_inventory_item",
        [
            "organization_id",
            "harness",
            "item_type",
            "source_type",
            "source_location",
            "identity_key",
        ],
    )
    op.drop_index(
        "ix_spm_inventory_item_org_harness_type_source",
        table_name="spm_inventory_item",
    )
    op.create_index(
        "ix_spm_asset_org_harness_type_artifact",
        "spm_inventory_item",
        ["organization_id", "harness", "item_type", "source_type"],
    )
    _rename_index("ix_spm_inventory_item_id", "ix_spm_asset_id")
    _rename_index("ix_spm_inventory_item_org_updated", "ix_spm_asset_org_updated")
    _rename_index("ix_spm_inventory_item_org_last_seen", "ix_spm_asset_org_last_seen")

    op.execute(
        f"""
        UPDATE spm_inventory_item
        SET source_type = {_source_type_case(SOURCE_TYPE_FROM_SNAKE)}
        """
    )
    op.execute(
        f"""
        UPDATE spm_finding
        SET source_type = {_source_type_case(SOURCE_TYPE_FROM_SNAKE)}
        """
    )

    op.drop_column("spm_finding", "item_location")
    op.alter_column(
        "spm_finding", "source_location", new_column_name="artifact_location"
    )
    op.alter_column("spm_finding", "source_type", new_column_name="artifact_type")
    op.alter_column("spm_finding", "item_type", new_column_name="asset_type")
    op.alter_column(
        "spm_finding",
        "inventory_observation_id",
        new_column_name="asset_sighting_id",
    )
    op.alter_column("spm_finding", "inventory_item_id", new_column_name="asset_id")

    op.alter_column(
        "spm_inventory_observation",
        "inventory_item_id",
        new_column_name="asset_id",
    )

    op.drop_column("spm_inventory_item", "item_location")
    op.alter_column(
        "spm_inventory_item",
        "source_location",
        new_column_name="artifact_location",
    )
    op.alter_column(
        "spm_inventory_item", "source_type", new_column_name="artifact_type"
    )
    op.alter_column("spm_inventory_item", "item_type", new_column_name="asset_type")

    op.rename_table("spm_inventory_observation", "spm_asset_sighting")
    op.rename_table("spm_inventory_item", "spm_asset")

    op.execute(enable_org_table_rls("spm_asset"))
    op.execute(enable_org_optional_workspace_table_rls("spm_asset_sighting"))
