"""Link schedules to triggers.

Revision ID: 61ebd010e726
Revises: a7c3e9d1f2b4
Create Date: 2026-11-27 18:00:00.000000

Expand only. Adds a nullable ``schedule.trigger_id`` with the same-workspace
key ``(trigger_id, workspace_id) -> trigger(id, workspace_id)`` that
``object_storage_trigger`` uses, so ``schedule`` becomes the ``schedule``
trigger kind's settings table. Backfills one ``trigger`` row and one workflow
``trigger_binding`` per schedule. The Temporal schedules keep reading
``schedule``; nothing about how schedules run changes.

A database trigger on ``schedule`` keeps the ``trigger`` row in step with the
schedule for every writer, the previous release included: deleting a schedule
(directly or by cascade from its workflow) deletes its ``trigger`` row, and a
status change is copied to ``trigger.status``.

Rolling deploy: the previous release still creates schedules without a
``trigger`` row while this one rolls out. Those stay out of the Triggers tab
until the next release reruns ``BACKFILL_SQL`` (it is idempotent) and makes the
column NOT NULL. This release adopts such a schedule when it next updates it.

The downgrade deletes the ``schedule`` trigger rows (the previous release
cannot read that kind) and drops what the upgrade added. Schedules themselves
are untouched either way.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "61ebd010e726"
down_revision: str | None = "a7c3e9d1f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_schedule_trigger_id_workspace_id_trigger"
_INDEX = "uq_schedule_trigger_id"
_SYNC_TRIGGER = "trg_schedule_trigger_sync"
_SYNC_FUNCTION = "tracecat_schedule_trigger_sync_v1"

# Tests install the function and trigger from these, so they cannot drift from
# what deployments run.
SYNC_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_SYNC_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $sync$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.trigger_id IS NOT NULL THEN
            DELETE FROM public."trigger"
            WHERE id = OLD.trigger_id AND workspace_id = OLD.workspace_id;
        END IF;
    ELSIF NEW.trigger_id IS NOT NULL AND NEW.status IS DISTINCT FROM OLD.status THEN
        UPDATE public."trigger"
        SET status = NEW.status, updated_at = now()
        WHERE id = NEW.trigger_id
            AND workspace_id = NEW.workspace_id
            AND status IS DISTINCT FROM NEW.status;
    END IF;
    RETURN NULL;
END
$sync$
"""
SYNC_TRIGGER_SQL = (
    f"CREATE TRIGGER {_SYNC_TRIGGER}"
    " AFTER DELETE OR UPDATE OF status ON public.schedule"
    f" FOR EACH ROW EXECUTE FUNCTION public.{_SYNC_FUNCTION}()"
)
DROP_SYNC_TRIGGER_SQL = f"DROP TRIGGER IF EXISTS {_SYNC_TRIGGER} ON public.schedule"

# One trigger row and one workflow binding per schedule that has none. Safe to
# run again: it only picks schedules whose trigger_id is still null, and locks
# them so a concurrent delete waits. The name is the workflow title plus
# " schedule", cut to fit the 255-character column. The trigger row keeps the
# schedule's created_at so the Triggers tab lists schedules by age.
BACKFILL_SQL = """
WITH pending AS (
    SELECT
        s.id AS schedule_id,
        gen_random_uuid() AS trigger_id,
        s.workspace_id,
        s.workflow_id,
        s.status,
        s.created_at,
        CASE
            WHEN w.title IS NULL OR btrim(w.title) = '' THEN 'Schedule'
            ELSE left(btrim(w.title), 246) || ' schedule'
        END AS name
    FROM schedule s
    LEFT JOIN workflow w ON w.id = s.workflow_id
    WHERE s.trigger_id IS NULL
    FOR UPDATE OF s
),
new_triggers AS (
    INSERT INTO "trigger" (id, workspace_id, kind, name, status, created_at)
    SELECT trigger_id, workspace_id, 'schedule', name, status, created_at
    FROM pending
),
new_bindings AS (
    INSERT INTO trigger_binding (
        id, workspace_id, trigger_id, target_type, workflow_id, status, created_at
    )
    SELECT
        gen_random_uuid(), workspace_id, trigger_id, 'workflow', workflow_id,
        'online', created_at
    FROM pending
    WHERE workflow_id IS NOT NULL
)
UPDATE schedule s
SET trigger_id = p.trigger_id
FROM pending p
WHERE s.id = p.schedule_id
"""


def upgrade() -> None:
    # The steps below run in two transactions (the autocommit block commits
    # the first), so each is idempotent and a failed upgrade can be retried.
    # The column, the NOT VALID key and the database trigger need brief
    # exclusive locks on schedule; fail fast rather than queue schedule reads
    # behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("ALTER TABLE schedule ADD COLUMN IF NOT EXISTS trigger_id UUID")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{_FK}' AND conrelid = 'schedule'::regclass
            ) THEN
                ALTER TABLE schedule ADD CONSTRAINT "{_FK}"
                    FOREIGN KEY (trigger_id, workspace_id)
                    REFERENCES "trigger" (id, workspace_id)
                    ON DELETE CASCADE NOT VALID;
            END IF;
        END
        $$
        """
    )
    op.execute(SYNC_FUNCTION_SQL)
    op.execute(DROP_SYNC_TRIGGER_SQL)
    op.execute(SYNC_TRIGGER_SQL)

    with op.get_context().autocommit_block():
        # Outside the DDL transaction the backfill takes only row locks, so
        # the previous release keeps reading and writing schedules meanwhile.
        op.execute(BACKFILL_SQL)
        # VALIDATE takes SHARE UPDATE EXCLUSIVE here: no blocking of reads or
        # writes during its scan.
        op.execute(f'ALTER TABLE schedule VALIDATE CONSTRAINT "{_FK}"')
        # A failed concurrent build leaves an invalid index; rebuild it so a
        # retry never adopts one.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX}"')
        op.execute(
            f'CREATE UNIQUE INDEX CONCURRENTLY "{_INDEX}" ON schedule (trigger_id)'
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(DROP_SYNC_TRIGGER_SQL)
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SYNC_FUNCTION}()")
    op.execute(f'ALTER TABLE schedule DROP CONSTRAINT IF EXISTS "{_FK}"')
    op.execute(f'DROP INDEX IF EXISTS "{_INDEX}"')
    op.execute("ALTER TABLE schedule DROP COLUMN IF EXISTS trigger_id")
    # The previous release has no schedule kind and fails on these rows.
    # Their bindings go with them.
    op.execute("""DELETE FROM "trigger" WHERE kind = 'schedule'""")
