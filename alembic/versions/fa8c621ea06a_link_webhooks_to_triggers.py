"""Link webhooks to triggers.

Revision ID: fa8c621ea06a
Revises: 61ebd010e726
Create Date: 2026-11-27 20:00:00.000000

Expand only. Adds a nullable ``webhook.trigger_id`` with the same-workspace
key ``(trigger_id, workspace_id) -> trigger(id, workspace_id)`` that
``schedule`` and ``object_storage_trigger`` use, so ``webhook`` becomes the
``webhook`` trigger kind's settings table. The key's delete action nulls only
``trigger_id``: deleting a webhook trigger leaves the workflow's webhook.

Every workflow has a webhook row from creation, most never used, so only
webhooks that are online now get a ``trigger`` row and one workflow
``trigger_binding``. The rest get theirs when they are next turned on (the
app does that), and the column stays nullable for good. The webhook router
keeps reading ``webhook``; nothing about how webhooks run changes.

A database trigger on ``webhook`` keeps the ``trigger`` row in step with the
webhook for every writer, the previous release included: deleting a webhook
(by cascade from its workflow) deletes its ``trigger`` row, and a status
change is copied to ``trigger.status``.

Rolling deploy: webhooks the previous release turns on while this one rolls
out get no ``trigger`` row until they are next turned on by this release, or
until a later release reruns ``backfill`` (it is idempotent).

The downgrade deletes the ``webhook`` trigger rows and drops what the upgrade
added. Webhooks themselves are untouched either way.
"""

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fa8c621ea06a"
down_revision: str | None = "61ebd010e726"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_webhook_trigger_id_workspace_id_trigger"
_INDEX = "uq_webhook_trigger_id"
_SYNC_TRIGGER = "trg_webhook_trigger_sync"
_SYNC_FUNCTION = "tracecat_webhook_trigger_sync_v1"

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
    " AFTER DELETE OR UPDATE OF status ON public.webhook"
    f" FOR EACH ROW EXECUTE FUNCTION public.{_SYNC_FUNCTION}()"
)
DROP_SYNC_TRIGGER_SQL = f"DROP TRIGGER IF EXISTS {_SYNC_TRIGGER} ON public.webhook"

# Run first, in the backfill's transaction (see ``backfill``): lock the online
# webhooks still without a trigger row, each with a key-share lock on its
# workflow (the binding insert needs one). Writers take these two rows in
# different orders (a workflow delete removes the webhook first; an import
# locks the workflow first), so the backfill never waits: a pair another
# transaction holds is skipped and retried on a later pass. Holding nothing
# while it waits, it cannot be part of a deadlock.
LOCK_WEBHOOKS_SQL = """
SELECT h.id
FROM webhook h
JOIN workflow w ON w.id = h.workflow_id AND w.workspace_id = h.workspace_id
WHERE h.trigger_id IS NULL AND h.status = 'online'
ORDER BY h.id
FOR UPDATE OF h SKIP LOCKED
FOR KEY SHARE OF w SKIP LOCKED
"""

# One trigger row and one workflow binding per online webhook that has none,
# among the webhooks ``LOCK_WEBHOOKS_SQL`` locked. A webhook turned on after
# that lock gets its row from the app when next turned on, or from a rerun.
# Offline webhooks get none (decision 5A: only used webhooks become
# triggers). Safe to run again: it only picks webhooks whose trigger_id is
# still null. The name is the workflow title plus " webhook", cut to fit the
# 255-character column. The trigger row keeps the webhook's created_at so the
# Triggers tab lists webhooks by age.
BACKFILL_SQL = """
WITH pending AS (
    SELECT
        h.id AS webhook_id,
        gen_random_uuid() AS trigger_id,
        h.workspace_id,
        h.workflow_id,
        h.status,
        h.created_at,
        CASE
            WHEN w.title IS NULL OR btrim(w.title) = '' THEN 'Webhook'
            ELSE left(btrim(w.title), 247) || ' webhook'
        END AS name
    FROM webhook h
    JOIN workflow w
        ON w.id = h.workflow_id AND w.workspace_id = h.workspace_id
    WHERE h.id = ANY(CAST(:webhook_ids AS uuid[]))
        AND h.trigger_id IS NULL
        AND h.status = 'online'
),
new_triggers AS (
    INSERT INTO "trigger" (id, workspace_id, kind, name, status, created_at)
    SELECT trigger_id, workspace_id, 'webhook', name, status, created_at
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
UPDATE webhook h
SET trigger_id = p.trigger_id
FROM pending p
WHERE h.id = p.webhook_id
"""


# Online webhooks still without a trigger row, such as ones a pass skipped.
PENDING_SQL = """
SELECT EXISTS (
    SELECT 1 FROM webhook
    WHERE trigger_id IS NULL AND status = 'online' AND workflow_id IS NOT NULL
)
"""
# A skipped pair is usually held for one short write. Passes repeat about
# once a second for up to this long; a webhook held for all of it is linked
# when it is next turned on, or when a later release reruns ``backfill``.
_BACKFILL_SECONDS = 30
_BACKFILL_RETRY_SECONDS = 1.0


def backfill(conn: sa.Connection) -> None:
    """Link the online webhooks without a trigger row that no one holds.

    Run inside one transaction. Never waits for a lock. Later releases may
    rerun it.
    """
    webhook_ids = list(conn.execute(sa.text(LOCK_WEBHOOKS_SQL)).scalars())
    conn.execute(sa.text(BACKFILL_SQL), {"webhook_ids": webhook_ids})


def upgrade() -> None:
    # The steps below run in two transactions (the autocommit block commits
    # the first), so each is idempotent and a failed upgrade can be retried.
    # The column, the NOT VALID key and the database trigger need brief
    # exclusive locks on webhook; fail fast rather than queue webhook reads
    # (every incoming webhook request reads the table) behind a long
    # transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("ALTER TABLE webhook ADD COLUMN IF NOT EXISTS trigger_id UUID")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{_FK}' AND conrelid = 'webhook'::regclass
            ) THEN
                ALTER TABLE webhook ADD CONSTRAINT "{_FK}"
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
        # the previous release keeps reading and writing webhooks meanwhile.
        # Each pass is its own transaction and skips rows others hold, so
        # repeat passes until none are left or time runs out.
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
        # VALIDATE takes SHARE UPDATE EXCLUSIVE here: no blocking of reads or
        # writes during its scan.
        op.execute(f'ALTER TABLE webhook VALIDATE CONSTRAINT "{_FK}"')
        # A failed concurrent build leaves an invalid index; rebuild it so a
        # retry never adopts one.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX}"')
        op.execute(
            f'CREATE UNIQUE INDEX CONCURRENTLY "{_INDEX}" ON webhook (trigger_id)'
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(DROP_SYNC_TRIGGER_SQL)
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SYNC_FUNCTION}()")
    op.execute(f'ALTER TABLE webhook DROP CONSTRAINT IF EXISTS "{_FK}"')
    op.execute(f'DROP INDEX IF EXISTS "{_INDEX}"')
    op.execute("ALTER TABLE webhook DROP COLUMN IF EXISTS trigger_id")
    # The previous release skips kinds it does not know, but the rows would
    # come back without settings on a later upgrade. Their bindings go with
    # them.
    op.execute("""DELETE FROM "trigger" WHERE kind = 'webhook'""")
