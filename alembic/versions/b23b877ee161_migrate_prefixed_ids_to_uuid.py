"""Migrate prefixed IDs to UUID

Revision ID: b23b877ee161
Revises: a1b2c3d4e5f6
Create Date: 2025-12-03

Migrates the following tables from prefixed string IDs to native PostgreSQL UUIDs:
- secret (prefix: secret-)
- organization_secret (prefix: secret-)
- workflow_definition (prefix: wf-defn-)
- webhook (prefix: wh-) - has FK from webhook_api_key
- schedule (prefix: sch-)
- action (prefix: act-) - has JSON refs in workflow.object
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.logger import logger

# revision identifiers, used by Alembic.
revision: str = "b23b877ee161"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables and their prefixes (prefix length includes the trailing dash)
SIMPLE_TABLES = {
    "secret": 7,  # "secret-" = 7 chars
    "organization_secret": 7,  # "secret-" = 7 chars
    "workflow_definition": 8,  # "wf-defn-" = 8 chars
    "schedule": 4,  # "sch-" = 4 chars
}


def upgrade() -> None:
    connection = op.get_bind()

    # =========================================================================
    # 1. Migrate simple tables (no FK references): secret, organization_secret,
    #    workflow_definition, schedule
    # =========================================================================
    for table, prefix_len in SIMPLE_TABLES.items():
        logger.info(f"Migrating {table}.id from prefixed string to UUID")

        # Add new UUID column
        op.add_column(table, sa.Column("id_new", sa.UUID(), nullable=True))

        # Populate new column by extracting UUID from prefixed ID
        connection.execute(
            sa.text(f"""
            UPDATE {table}
            SET id_new = CAST(substring(id from {prefix_len + 1}) AS uuid)
        """)
        )

        # Make the new column NOT NULL
        op.alter_column(table, "id_new", nullable=False)

        # Drop the unique index on id (all tables use ix_<table>_id pattern)
        op.drop_index(f"ix_{table}_id", table_name=table)

        # Drop old column and rename new column
        op.drop_column(table, "id")
        op.alter_column(table, "id_new", new_column_name="id")

        # Recreate unique index
        op.create_index(f"ix_{table}_id", table, ["id"], unique=True)

    # =========================================================================
    # 2. Migrate webhook table (has FK from webhook_api_key)
    # =========================================================================
    logger.info("Migrating webhook.id from prefixed string to UUID")

    # Drop the FK constraint from webhook_api_key first
    op.drop_constraint(
        "fk_webhook_api_key_webhook_id_webhook", "webhook_api_key", type_="foreignkey"
    )

    # Drop the unique constraint on webhook_api_key.webhook_id
    op.drop_constraint(
        "uq_webhook_api_key_webhook_id", "webhook_api_key", type_="unique"
    )

    # Add new UUID columns to both tables
    op.add_column("webhook", sa.Column("id_new", sa.UUID(), nullable=True))
    op.add_column(
        "webhook_api_key", sa.Column("webhook_id_new", sa.UUID(), nullable=True)
    )

    # Populate webhook.id_new (wh- = 3 chars)
    connection.execute(
        sa.text("""
        UPDATE webhook
        SET id_new = CAST(substring(id from 4) AS uuid)
    """)
    )

    # Populate webhook_api_key.webhook_id_new by joining with webhook
    connection.execute(
        sa.text("""
        UPDATE webhook_api_key wak
        SET webhook_id_new = w.id_new
        FROM webhook w
        WHERE wak.webhook_id = w.id
    """)
    )

    # Make webhook.id_new NOT NULL
    op.alter_column("webhook", "id_new", nullable=False)

    # Drop unique index on webhook.id
    op.drop_index("ix_webhook_id", table_name="webhook")

    # Drop old columns and rename new columns
    op.drop_column("webhook", "id")
    op.alter_column("webhook", "id_new", new_column_name="id")

    op.drop_column("webhook_api_key", "webhook_id")
    op.alter_column("webhook_api_key", "webhook_id_new", new_column_name="webhook_id")

    # Recreate unique index on webhook.id
    op.create_index("ix_webhook_id", "webhook", ["id"], unique=True)

    # Recreate unique constraint on webhook_api_key.webhook_id
    op.create_unique_constraint(
        "uq_webhook_api_key_webhook_id", "webhook_api_key", ["webhook_id"]
    )

    # Recreate FK constraint
    op.create_foreign_key(
        "fk_webhook_api_key_webhook_id_webhook",
        "webhook_api_key",
        "webhook",
        ["webhook_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # =========================================================================
    # 3. Migrate action table (has JSON refs in workflow.object)
    # =========================================================================
    logger.info("Migrating action.id from prefixed string to UUID")

    # Add new UUID column to action
    op.add_column("action", sa.Column("id_new", sa.UUID(), nullable=True))

    # Populate action.id_new (act- = 4 chars)
    connection.execute(
        sa.text("""
        UPDATE action
        SET id_new = CAST(substring(id from 5) AS uuid)
    """)
    )

    # Update workflow.object JSON - replace action IDs in nodes[].id
    logger.info("Updating workflow.object JSON with new action UUIDs")
    connection.execute(
        sa.text("""
        WITH action_mapping AS (
            SELECT id AS old_id, id_new AS new_id FROM action
        )
        UPDATE workflow w
        SET object = jsonb_set(
            w.object,
            '{nodes}',
            (
                SELECT COALESCE(
                    jsonb_agg(
                        CASE
                            WHEN m.new_id IS NOT NULL
                            THEN jsonb_set(node, '{id}', to_jsonb(m.new_id::text))
                            ELSE node
                        END
                        ORDER BY ordinality
                    ),
                    '[]'::jsonb
                )
                FROM jsonb_array_elements(w.object->'nodes') WITH ORDINALITY AS t(node, ordinality)
                LEFT JOIN action_mapping m ON node->>'id' = m.old_id
            )
        )
        WHERE w.object IS NOT NULL
          AND w.object->'nodes' IS NOT NULL
          AND jsonb_array_length(w.object->'nodes') > 0
    """)
    )

    # Update workflow.object JSON - replace action IDs in edges[].source and edges[].target
    logger.info("Updating workflow.object edges with new action UUIDs")
    connection.execute(
        sa.text("""
        WITH action_mapping AS (
            SELECT id AS old_id, id_new AS new_id FROM action
        )
        UPDATE workflow w
        SET object = jsonb_set(
            w.object,
            '{edges}',
            (
                SELECT COALESCE(
                    jsonb_agg(
                        (
                            SELECT
                                CASE
                                    WHEN m_tgt.new_id IS NOT NULL
                                    THEN jsonb_set(edge_with_source, '{target}', to_jsonb(m_tgt.new_id::text))
                                    ELSE edge_with_source
                                END
                            FROM (
                                SELECT
                                    CASE
                                        WHEN m_src.new_id IS NOT NULL
                                        THEN jsonb_set(edge, '{source}', to_jsonb(m_src.new_id::text))
                                        ELSE edge
                                    END AS edge_with_source
                                FROM action_mapping m_src
                                WHERE edge->>'source' = m_src.old_id
                                UNION ALL
                                SELECT edge AS edge_with_source
                                WHERE NOT EXISTS (
                                    SELECT 1 FROM action_mapping m_src
                                    WHERE edge->>'source' = m_src.old_id
                                )
                            ) source_sub
                            LEFT JOIN action_mapping m_tgt ON edge->>'target' = m_tgt.old_id
                            LIMIT 1
                        )
                        ORDER BY ordinality
                    ),
                    '[]'::jsonb
                )
                FROM jsonb_array_elements(w.object->'edges') WITH ORDINALITY AS t(edge, ordinality)
            )
        )
        WHERE w.object IS NOT NULL
          AND w.object->'edges' IS NOT NULL
          AND jsonb_array_length(w.object->'edges') > 0
    """)
    )

    # Update workflow.entrypoint - convert prefixed action ID to UUID
    logger.info("Updating workflow.entrypoint with new action UUIDs")
    connection.execute(
        sa.text("""
        UPDATE workflow w
        SET entrypoint = a.id_new::text
        FROM action a
        WHERE w.entrypoint = a.id
          AND w.entrypoint IS NOT NULL
    """)
    )

    # Make action.id_new NOT NULL
    op.alter_column("action", "id_new", nullable=False)

    # Drop unique index on action.id
    op.drop_index("ix_action_id", table_name="action")

    # Drop old column and rename new column
    op.drop_column("action", "id")
    op.alter_column("action", "id_new", new_column_name="id")

    # Recreate unique index
    op.create_index("ix_action_id", "action", ["id"], unique=True)

    logger.info("Migration complete: all prefixed IDs converted to UUIDs")


def downgrade() -> None:
    connection = op.get_bind()

    # =========================================================================
    # 1. Downgrade action table
    # =========================================================================
    logger.info("Downgrading action.id from UUID to prefixed string")

    # Add old column
    op.add_column("action", sa.Column("id_old", sa.String(64), nullable=True))

    # Populate old column
    connection.execute(
        sa.text("""
        UPDATE action
        SET id_old = 'act-' || replace(id::text, '-', '')
    """)
    )

    # Update workflow.object JSON - restore prefixed IDs in nodes
    # Use string comparison (id::text) to avoid casting non-UUID strings like "trigger-xxx"
    connection.execute(
        sa.text("""
        WITH action_mapping AS (
            SELECT id::text AS new_id, id_old AS old_id FROM action
        )
        UPDATE workflow w
        SET object = jsonb_set(
            w.object,
            '{nodes}',
            (
                SELECT COALESCE(
                    jsonb_agg(
                        CASE
                            WHEN m.old_id IS NOT NULL
                            THEN jsonb_set(node, '{id}', to_jsonb(m.old_id))
                            ELSE node
                        END
                        ORDER BY ordinality
                    ),
                    '[]'::jsonb
                )
                FROM jsonb_array_elements(w.object->'nodes') WITH ORDINALITY AS t(node, ordinality)
                LEFT JOIN action_mapping m ON node->>'id' = m.new_id
            )
        )
        WHERE w.object IS NOT NULL
          AND w.object->'nodes' IS NOT NULL
          AND jsonb_array_length(w.object->'nodes') > 0
    """)
    )

    # Update workflow.object edges - restore prefixed IDs
    # Use string comparison to avoid casting non-UUID strings
    connection.execute(
        sa.text("""
        WITH action_mapping AS (
            SELECT id::text AS new_id, id_old AS old_id FROM action
        )
        UPDATE workflow w
        SET object = jsonb_set(
            w.object,
            '{edges}',
            (
                SELECT COALESCE(
                    jsonb_agg(
                        (
                            SELECT
                                CASE
                                    WHEN m_tgt.old_id IS NOT NULL
                                    THEN jsonb_set(edge_with_source, '{target}', to_jsonb(m_tgt.old_id))
                                    ELSE edge_with_source
                                END
                            FROM (
                                SELECT
                                    CASE
                                        WHEN m_src.old_id IS NOT NULL
                                        THEN jsonb_set(edge, '{source}', to_jsonb(m_src.old_id))
                                        ELSE edge
                                    END AS edge_with_source
                                FROM action_mapping m_src
                                WHERE edge->>'source' = m_src.new_id
                                UNION ALL
                                SELECT edge AS edge_with_source
                                WHERE NOT EXISTS (
                                    SELECT 1 FROM action_mapping m_src
                                    WHERE edge->>'source' = m_src.new_id
                                )
                            ) source_sub
                            LEFT JOIN action_mapping m_tgt ON edge->>'target' = m_tgt.new_id
                            LIMIT 1
                        )
                        ORDER BY ordinality
                    ),
                    '[]'::jsonb
                )
                FROM jsonb_array_elements(w.object->'edges') WITH ORDINALITY AS t(edge, ordinality)
            )
        )
        WHERE w.object IS NOT NULL
          AND w.object->'edges' IS NOT NULL
          AND jsonb_array_length(w.object->'edges') > 0
    """)
    )

    # Restore workflow.entrypoint to prefixed action ID
    connection.execute(
        sa.text("""
        UPDATE workflow w
        SET entrypoint = a.id_old
        FROM action a
        WHERE w.entrypoint = a.id::text
          AND w.entrypoint IS NOT NULL
    """)
    )

    op.alter_column("action", "id_old", nullable=False)
    op.drop_index("ix_action_id", table_name="action")
    op.drop_column("action", "id")
    op.alter_column("action", "id_old", new_column_name="id")
    op.create_index("ix_action_id", "action", ["id"], unique=True)

    # =========================================================================
    # 2. Downgrade webhook table
    # =========================================================================
    logger.info("Downgrading webhook.id from UUID to prefixed string")

    op.drop_constraint(
        "fk_webhook_api_key_webhook_id_webhook", "webhook_api_key", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_webhook_api_key_webhook_id", "webhook_api_key", type_="unique"
    )

    op.add_column("webhook", sa.Column("id_old", sa.String(64), nullable=True))
    op.add_column(
        "webhook_api_key", sa.Column("webhook_id_old", sa.String(64), nullable=True)
    )

    connection.execute(
        sa.text("""
        UPDATE webhook
        SET id_old = 'wh-' || replace(id::text, '-', '')
    """)
    )

    connection.execute(
        sa.text("""
        UPDATE webhook_api_key wak
        SET webhook_id_old = w.id_old
        FROM webhook w
        WHERE wak.webhook_id = w.id
    """)
    )

    op.alter_column("webhook", "id_old", nullable=False)
    op.drop_index("ix_webhook_id", table_name="webhook")
    op.drop_column("webhook", "id")
    op.alter_column("webhook", "id_old", new_column_name="id")

    op.drop_column("webhook_api_key", "webhook_id")
    op.alter_column("webhook_api_key", "webhook_id_old", new_column_name="webhook_id")

    op.create_index("ix_webhook_id", "webhook", ["id"], unique=True)
    op.create_unique_constraint(
        "uq_webhook_api_key_webhook_id", "webhook_api_key", ["webhook_id"]
    )
    op.create_foreign_key(
        "fk_webhook_api_key_webhook_id_webhook",
        "webhook_api_key",
        "webhook",
        ["webhook_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # =========================================================================
    # 3. Downgrade simple tables
    # =========================================================================
    prefixes = {
        "secret": "secret-",
        "organization_secret": "secret-",
        "workflow_definition": "wf-defn-",
        "schedule": "sch-",
    }

    for table, prefix in prefixes.items():
        logger.info(f"Downgrading {table}.id from UUID to prefixed string")

        op.add_column(table, sa.Column("id_old", sa.String(255), nullable=True))

        connection.execute(
            sa.text(f"""
            UPDATE {table}
            SET id_old = '{prefix}' || replace(id::text, '-', '')
        """)
        )

        op.alter_column(table, "id_old", nullable=False)
        op.drop_index(f"ix_{table}_id", table_name=table)
        op.drop_column(table, "id")
        op.alter_column(table, "id_old", new_column_name="id")
        op.create_index(f"ix_{table}_id", table, ["id"], unique=True)

    logger.info("Downgrade complete: all UUIDs converted back to prefixed IDs")
