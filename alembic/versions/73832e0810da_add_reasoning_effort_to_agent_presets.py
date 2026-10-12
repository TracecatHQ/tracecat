"""add reasoning_effort to agent presets

Revision ID: 73832e0810da
Revises: e379e38b8495
Create Date: 2026-09-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73832e0810da"
down_revision: str | None = "e379e38b8495"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent_preset", "agent_preset_version")

# Older application versions read and write only enable_thinking, so keep both
# columns in sync for every writer until a contract migration drops the flag.
# A reasoning_effort change wins. A change to enable_thinking alone is an older
# app's write: off maps to "off", and on clears "off" back to the model default
# while keeping any other level.
SYNC_FUNCTION_SQL = """
    CREATE FUNCTION sync_agent_preset_reasoning_effort() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.reasoning_effort IS NULL AND NOT NEW.enable_thinking THEN
                NEW.reasoning_effort := 'off';
            END IF;
        ELSIF NEW.reasoning_effort IS NOT DISTINCT FROM OLD.reasoning_effort
            AND NEW.enable_thinking IS DISTINCT FROM OLD.enable_thinking THEN
            IF NOT NEW.enable_thinking THEN
                NEW.reasoning_effort := 'off';
            ELSIF NEW.reasoning_effort = 'off' THEN
                NEW.reasoning_effort := NULL;
            END IF;
        END IF;
        NEW.enable_thinking := NEW.reasoning_effort IS DISTINCT FROM 'off';
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    # Expand only: enable_thinking stays so a rolled-back app keeps each
    # preset's choice. Enabled presets map to NULL (model default); disabled
    # presets map to "off".
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        )
        op.execute(
            sa.text(
                f"UPDATE {table} SET reasoning_effort = 'off' "
                "WHERE enable_thinking IS FALSE"
            )
        )
    op.execute(sa.text(SYNC_FUNCTION_SQL))
    for table in _TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_sync_{table}_reasoning_effort "
                f"BEFORE INSERT OR UPDATE OF reasoning_effort, enable_thinking "
                f"ON {table} FOR EACH ROW "
                "EXECUTE FUNCTION sync_agent_preset_reasoning_effort()"
            )
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(
            sa.text(f"DROP TRIGGER trg_sync_{table}_reasoning_effort ON {table}")
        )
    op.execute(sa.text("DROP FUNCTION sync_agent_preset_reasoning_effort()"))
    for table in reversed(_TABLES):
        op.drop_column(table, "reasoning_effort")
