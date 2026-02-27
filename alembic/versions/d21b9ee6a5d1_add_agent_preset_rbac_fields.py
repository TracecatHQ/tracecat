"""add agent preset rbac fields

Revision ID: d21b9ee6a5d1
Revises: c9e4f54f0a2b
Create Date: 2026-02-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d21b9ee6a5d1"
down_revision: str | None = "c9e4f54f0a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "agent_preset",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "agent_preset",
        sa.Column("assigned_role_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_preset_assigned_role_id"),
        "agent_preset",
        ["assigned_role_id"],
        unique=False,
    )
    op.create_foreign_key(
        "agent_preset_assigned_role_id_fkey",
        "agent_preset",
        "role",
        ["assigned_role_id"],
        ["id"],
        ondelete="SET NULL",
    )

    bind.exec_driver_sql(
        """
        INSERT INTO scope (
            id,
            name,
            resource,
            action,
            description,
            source,
            source_ref,
            organization_id
        )
        VALUES
            (
                gen_random_uuid(),
                'agent:preset:*:read',
                'agent:preset',
                'read',
                'View all agent presets',
                'PLATFORM'::scopesource,
                'agent_preset:wildcard',
                NULL
            ),
            (
                gen_random_uuid(),
                'agent:preset:*:execute',
                'agent:preset',
                'execute',
                'Execute all agent presets',
                'PLATFORM'::scopesource,
                'agent_preset:wildcard',
                NULL
            ),
            (
                gen_random_uuid(),
                'agent:preset:*:update',
                'agent:preset',
                'update',
                'Update all agent presets',
                'PLATFORM'::scopesource,
                'agent_preset:wildcard',
                NULL
            ),
            (
                gen_random_uuid(),
                'agent:preset:*:delete',
                'agent:preset',
                'delete',
                'Delete all agent presets',
                'PLATFORM'::scopesource,
                'agent_preset:wildcard',
                NULL
            )
        ON CONFLICT (name)
        WHERE organization_id IS NULL
        DO NOTHING
        """
    )

    bind.exec_driver_sql(
        """
        INSERT INTO scope (
            id,
            name,
            resource,
            action,
            description,
            source,
            source_ref,
            organization_id
        )
        SELECT
            gen_random_uuid(),
            format('agent:preset:%s:%s', p.slug, action_map.action),
            'agent:preset',
            action_map.action,
            format('Allow %s access to agent preset ''%s''', action_map.action, p.slug),
            'PLATFORM'::scopesource,
            format('agent_preset:%s', p.slug),
            NULL
        FROM (
            SELECT DISTINCT slug
            FROM agent_preset
            WHERE slug IS NOT NULL
        ) AS p
        CROSS JOIN (
            VALUES
                ('read'),
                ('execute'),
                ('update'),
                ('delete')
        ) AS action_map(action)
        ON CONFLICT (name)
        WHERE organization_id IS NULL
        DO NOTHING
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_constraint(
        "agent_preset_assigned_role_id_fkey",
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_agent_preset_assigned_role_id"), table_name="agent_preset")
    op.drop_column("agent_preset", "assigned_role_id")
    op.drop_column("agent_preset", "is_system")

    bind.exec_driver_sql(
        """
        DELETE FROM scope
        WHERE organization_id IS NULL
          AND resource = 'agent:preset'
        """
    )
