"""Link case triggers to triggers.

Revision ID: 9620041994d1
Revises: fa8c621ea06a
Create Date: 2026-11-27 22:00:00.000000

Expand only. Adds a nullable ``case_trigger.trigger_id`` with the
same-workspace key ``(trigger_id, workspace_id) -> trigger(id, workspace_id)``
that ``schedule``, ``webhook`` and ``object_storage_trigger`` use, so
``case_trigger`` becomes the ``case_trigger`` trigger kind's settings table.
The key's delete action nulls only ``trigger_id``: deleting a case trigger's
trigger leaves the workflow's case trigger.

Every workflow has a case trigger row from creation, most never used, so only
case triggers that are online now get a ``trigger`` row and one workflow
``trigger_binding``. The rest get theirs when they are next turned on (the
app does that), and the column stays nullable for good. The case trigger
outbox and dispatcher keep reading ``case_trigger``; nothing about how case
triggers run changes.

A database trigger on ``case_trigger`` keeps the ``trigger`` row in step with
the case trigger for every writer, the previous release and the dispatcher
included: deleting a case trigger (by cascade from its workflow) deletes its
``trigger`` row, and a status change is copied to ``trigger.status``.

Rolling deploy: case triggers the previous release turns on while this one
rolls out get no ``trigger`` row until they are next turned on by this
release, or until a later release reruns ``backfill`` (it is idempotent).

The downgrade deletes the ``case_trigger`` trigger rows of every workspace in
the database (it is a whole-database rollback, like every downgrade here) and
drops what the upgrade added. Case triggers themselves are untouched either
way.
"""

import logging
import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision: str = "9620041994d1"
down_revision: str | None = "fa8c621ea06a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_case_trigger_trigger_id_workspace_id_trigger"
_INDEX = "uq_case_trigger_trigger_id"
_SYNC_TRIGGER = "trg_case_trigger_trigger_sync"
_SYNC_FUNCTION = "tracecat_case_trigger_trigger_sync_v1"

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
    " AFTER DELETE OR UPDATE OF status ON public.case_trigger"
    f" FOR EACH ROW EXECUTE FUNCTION public.{_SYNC_FUNCTION}()"
)
DROP_SYNC_TRIGGER_SQL = f"DROP TRIGGER IF EXISTS {_SYNC_TRIGGER} ON public.case_trigger"

# Run first, in the backfill's transaction (see ``backfill``): lock the online
# case triggers still without a trigger row, each with a key-share lock on its
# workflow (the binding insert needs one). Writers take these two rows in
# different orders (a workflow delete removes the case trigger first; the case
# trigger service and imports lock the workflow first), so the backfill never
# waits: a pair another transaction holds is skipped and retried on a later
# pass. Never waiting, it cannot be part of a deadlock. Postgres may keep the
# lock it did get on a skipped pair until the pass commits, so another writer
# can wait for one short pass, never longer.
LOCK_CASE_TRIGGERS_SQL = """
SELECT ct.id
FROM case_trigger ct
JOIN workflow w ON w.id = ct.workflow_id AND w.workspace_id = ct.workspace_id
WHERE ct.trigger_id IS NULL AND ct.status = 'online'
ORDER BY ct.id
FOR UPDATE OF ct SKIP LOCKED
FOR KEY SHARE OF w SKIP LOCKED
"""

# One trigger row and one workflow binding per online case trigger that has
# none, among the case triggers ``LOCK_CASE_TRIGGERS_SQL`` locked. A case
# trigger turned on after that lock gets its row from the app when next turned
# on, or from a rerun. Offline case triggers get none (decision 5A: only used
# case triggers become triggers). Safe to run again: it only picks case
# triggers whose trigger_id is still null. The name is the workflow title plus
# " case trigger", cut to fit the 255-character column. The trigger row keeps
# the case trigger's created_at so the Triggers tab lists them by age.
BACKFILL_SQL = """
WITH pending AS (
    SELECT
        ct.id AS case_trigger_id,
        gen_random_uuid() AS trigger_id,
        ct.workspace_id,
        ct.workflow_id,
        ct.status,
        ct.created_at,
        CASE
            WHEN w.title IS NULL OR btrim(w.title) = '' THEN 'Case trigger'
            ELSE left(btrim(w.title), 242) || ' case trigger'
        END AS name
    FROM case_trigger ct
    JOIN workflow w
        ON w.id = ct.workflow_id AND w.workspace_id = ct.workspace_id
    WHERE ct.id = ANY(CAST(:case_trigger_ids AS uuid[]))
        AND ct.trigger_id IS NULL
        AND ct.status = 'online'
),
new_triggers AS (
    INSERT INTO "trigger" (id, workspace_id, kind, name, status, created_at)
    SELECT trigger_id, workspace_id, 'case_trigger', name, status, created_at
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
)
UPDATE case_trigger ct
SET trigger_id = p.trigger_id
FROM pending p
WHERE ct.id = p.case_trigger_id
"""


# Online case triggers still without a trigger row, such as ones a pass
# skipped.
PENDING_SQL = """
SELECT EXISTS (
    SELECT 1 FROM case_trigger WHERE trigger_id IS NULL AND status = 'online'
)
"""
# The same rows counted, once, for the log line after the last pass.
PENDING_COUNT_SQL = """
SELECT count(*) FROM case_trigger WHERE trigger_id IS NULL AND status = 'online'
"""
# A skipped pair is usually held for one short write. Passes repeat about
# once a second for up to this long; a case trigger held for all of it is
# linked when it is next turned on, or when a later release reruns
# ``backfill``.
_BACKFILL_SECONDS = 30
_BACKFILL_RETRY_SECONDS = 1.0


def backfill(conn: sa.Connection) -> None:
    """Link the online case triggers without a trigger row that no one holds.

    Run inside one transaction. Never waits for a lock. Later releases may
    rerun it.
    """
    case_trigger_ids = list(conn.execute(sa.text(LOCK_CASE_TRIGGERS_SQL)).scalars())
    conn.execute(sa.text(BACKFILL_SQL), {"case_trigger_ids": case_trigger_ids})


def upgrade() -> None:
    # The steps below run in two transactions (the autocommit block commits
    # the first), so each is idempotent and a failed upgrade can be retried.
    # The column, the NOT VALID key and the database trigger need brief
    # exclusive locks on case_trigger; fail fast rather than queue case writes
    # (each one reads the table for online triggers) behind a long
    # transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("ALTER TABLE case_trigger ADD COLUMN IF NOT EXISTS trigger_id UUID")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{_FK}' AND conrelid = 'case_trigger'::regclass
            ) THEN
                ALTER TABLE case_trigger ADD CONSTRAINT "{_FK}"
                    FOREIGN KEY (trigger_id, workspace_id)
                    REFERENCES "trigger" (id, workspace_id)
                    ON DELETE SET NULL (trigger_id) NOT VALID;
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
        # the previous release keeps reading and writing case triggers
        # meanwhile. Each pass is its own transaction and skips rows others
        # hold, so repeat passes until none are left or time runs out.
        conn = op.get_bind()
        deadline = time.monotonic() + _BACKFILL_SECONDS
        while True:
            op.execute("BEGIN")
            backfill(conn)
            op.execute("COMMIT")
            if not conn.execute(sa.text(PENDING_SQL)).scalar():
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(_BACKFILL_RETRY_SECONDS)
        pending = conn.execute(sa.text(PENDING_COUNT_SQL)).scalar() or 0
        # Accepted gap: these keep running but are not listed in the Triggers
        # tab until they are next turned on or ``backfill`` is rerun. Count
        # only, so no workflow or case trigger IDs reach the logs.
        logger.info(
            "Case trigger backfill done; %d online case triggers still unlinked",
            pending,
        )
        # VALIDATE takes SHARE UPDATE EXCLUSIVE here: no blocking of reads or
        # writes during its scan.
        op.execute(f'ALTER TABLE case_trigger VALIDATE CONSTRAINT "{_FK}"')
        # A failed concurrent build leaves an invalid index; rebuild it so a
        # retry never adopts one.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX}"')
        op.execute(
            f'CREATE UNIQUE INDEX CONCURRENTLY "{_INDEX}" ON case_trigger (trigger_id)'
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(DROP_SYNC_TRIGGER_SQL)
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SYNC_FUNCTION}()")
    op.execute(f'ALTER TABLE case_trigger DROP CONSTRAINT IF EXISTS "{_FK}"')
    op.execute(f'DROP INDEX IF EXISTS "{_INDEX}"')
    op.execute("ALTER TABLE case_trigger DROP COLUMN IF EXISTS trigger_id")
    # The previous release skips kinds it does not know, but the rows would
    # come back without settings on a later upgrade. Their bindings go with
    # them.
    op.execute("""DELETE FROM "trigger" WHERE kind = 'case_trigger'""")
