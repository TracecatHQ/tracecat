"""Keep channel bots in their agent preset's workspace

Revision ID: 43d161879873
Revises: e20249352688
Create Date: 2026-11-14 19:50:08.874078

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "43d161879873"
down_revision: str | None = "e20249352688"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The keys lock agent_preset and agent_channel_bot; fail fast rather than
    # queue behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_unique_constraint(
        op.f("uq_agent_preset_id_workspace_id"), "agent_preset", ["id", "workspace_id"]
    )
    op.drop_constraint(
        "fk_agent_channel_bot_agent_preset_id_agent_preset",
        "agent_channel_bot",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_agent_channel_bot_agent_preset_id_workspace_id_agent_preset"),
        "agent_channel_bot",
        "agent_preset",
        ["agent_preset_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_constraint(
        op.f("fk_agent_channel_bot_agent_preset_id_workspace_id_agent_preset"),
        "agent_channel_bot",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_agent_channel_bot_agent_preset_id_agent_preset",
        "agent_channel_bot",
        "agent_preset",
        ["agent_preset_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        op.f("uq_agent_preset_id_workspace_id"), "agent_preset", type_="unique"
    )
