"""guard rls on dynamic workspace tables

Revision ID: b4074a0896b5
Revises: ef408b9720a6
Create Date: 2026-11-06 23:00:00.000000

The app role inherits owner rights on dynamic workspace tables (`tables_*`,
`custom_fields_*`) through its membership in the dynamic owner role
(`TRACECAT__DB_DYNAMIC_OWNER_ROLE`). A table owner can switch RLS off with
`DISABLE` / `NO FORCE ROW LEVEL SECURITY`, `DROP POLICY` or `ALTER POLICY`.
This revision installs an event trigger that rejects those statements unless
the session's login role (`session_user`) is a member of the trigger's owner:
the role that ran this migration, or a superuser. `SET ROLE` does not change
`session_user`, so the app can't lift the guard by switching to the owner
role.

Blocked for other sessions, on tables in workspace schemas that have at least
one policy:
- disabling or un-forcing RLS;
- dropping a policy while its table survives (`DROP POLICY`,
  `DROP COLUMN ... CASCADE`);
- `ALTER POLICY`;
- a second permissive policy;
- inheritance or partitioning, with the workspace table as parent or child;
- moving a table with the workspace policy out of a workspace schema, by
  `ALTER TABLE ... SET SCHEMA` or by renaming the schema.

The app's runtime DDL still works: creating schemas and tables, the
ENABLE/FORCE/CREATE POLICY sequence, column changes, indexes, and dropping a
whole table or schema.

Limits:
- The guard only binds a login that is neither a superuser nor a member of
  the trigger owner. Where the app logs in as the migration role (the Compose
  default `postgres`, or any single-role install) it has no effect; it needs a
  separate app login such as staging's `app`.
- It covers DDL only. `app.rls_bypass` and `app.current_workspace_id` are
  custom settings any role can set, so arbitrary SQL as the app role still
  bypasses every policy. RLS guards against missing tenant filters in app
  queries, not against SQL injection.
- It doesn't repair changes made before it was installed. `AUDIT_SQL` lists
  dynamic tables whose RLS differs from what the app creates.

`CREATE EVENT TRIGGER` needs a superuser (`rds_superuser` on RDS). When the
migration role can't create it, the upgrade logs a warning and skips the
guard rather than failing: the guard only matters with
`TRACECAT__RLS_MODE=enforce`. The upgrade first drops any existing guard
objects; any failure other than creating the event triggers, such as an
existing guard owned by another role, fails the upgrade. An admin can install the
guard later by running the statements in `GUARD_STATEMENTS` in order, as a
superuser, against the Tracecat database; see `MANUAL_INSTALL_SQL`. Run
migrations as that admin, or as a member of it, so they keep full DDL rights
on dynamic tables.

Managed Postgres blocks major version upgrades while event triggers exist
(RDS and Azure Flexible Server both say to drop them first). Drop the guard
with the statements in `UNINSTALL_STATEMENTS` before a major upgrade, and run
`GUARD_STATEMENTS` again afterwards.

The upgrade also enables and forces RLS on any dynamic table that has RLS
enabled or a policy but isn't both enabled and forced, for example one an
older app version created after b0debed4e0c6 ran. Without that, the guard
would reject later column changes on such a table. Downgrade leaves those
flags in place: b0debed4e0c6 and the app own that state.
"""

import logging
from collections.abc import Sequence

import psycopg.errors
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4074a0896b5"
down_revision: str | None = "ef408b9720a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

GUARD_FUNCTION = "public.tracecat_guard_dynamic_rls()"
DDL_TRIGGER = "tracecat_guard_dynamic_rls_ddl"
DROP_TRIGGER = "tracecat_guard_dynamic_rls_drop"

# Backslash escapes the LIKE wildcard so `tablesX...` schemas don't match.
_GUARD_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION public.tracecat_guard_dynamic_rls()
RETURNS event_trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $guard$
DECLARE
    guard_owner oid;
    cmd record;
    target oid;
    problem text;
BEGIN
    SELECT evtowner INTO guard_owner
    FROM pg_event_trigger
    WHERE evtfoid = 'public.tracecat_guard_dynamic_rls()'::regprocedure
      AND evtevent = TG_EVENT
    LIMIT 1;
    -- session_user, not current_user: SET ROLE to the owner role must not
    -- lift the guard. Superusers are members of every role.
    IF pg_has_role(session_user, guard_owner, 'MEMBER') THEN
        RETURN;
    END IF;

    IF TG_EVENT = 'sql_drop' THEN
        -- A policy may go only with its table (DROP TABLE, DROP SCHEMA).
        SELECT format('%s: policies on workspace tables cannot be dropped',
                      d.object_identity)
        INTO problem
        FROM pg_event_trigger_dropped_objects() d
        WHERE d.object_type = 'policy'
          AND (d.address_names[1] LIKE 'tables\_%'
               OR d.address_names[1] LIKE 'custom\_fields\_%'
               OR d.address_names[3] = 'rls_policy_dynamic_workspace')
          AND to_regclass(format('%I.%I', d.address_names[1],
                                 d.address_names[2])) IS NOT NULL
        LIMIT 1;
    ELSE
        FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
            IF cmd.classid = 'pg_namespace'::regclass THEN
                -- Renaming a schema must not carry workspace tables out of
                -- the guarded names.
                SELECT format('schema %I holds workspace tables and must '
                              'keep a workspace schema name', n.nspname)
                INTO problem
                FROM pg_namespace n
                WHERE n.oid = cmd.objid
                  AND n.nspname NOT LIKE 'tables\_%'
                  AND n.nspname NOT LIKE 'custom\_fields\_%'
                  AND EXISTS (
                      SELECT 1
                      FROM pg_class c
                      JOIN pg_policy p ON p.polrelid = c.oid
                      WHERE c.relnamespace = n.oid
                        AND p.polname = 'rls_policy_dynamic_workspace'
                  );
            ELSE
                IF cmd.classid = 'pg_policy'::regclass THEN
                    SELECT p.polrelid INTO target
                    FROM pg_policy p WHERE p.oid = cmd.objid;
                ELSIF cmd.classid = 'pg_class'::regclass THEN
                    target := cmd.objid;
                ELSE
                    CONTINUE;
                END IF;
                -- Check the table, its children and its parents: ATTACH
                -- PARTITION reports only the parent, and INHERITS reports only
                -- the child.
                SELECT format('%s: %s', t.rel::regclass, t.reason)
                INTO problem
                FROM (
                    SELECT c.oid AS rel,
                        CASE
                            WHEN NOT s.dynamic THEN
                                'workspace tables must stay in their workspace schema'
                            WHEN cmd.command_tag = 'ALTER POLICY' THEN
                                'policies on workspace tables cannot be altered'
                            WHEN NOT c.relrowsecurity THEN
                                'row level security must stay enabled'
                            WHEN NOT c.relforcerowsecurity THEN
                                'row level security must stay forced'
                            WHEN pol.permissive > 1 THEN
                                'only the workspace policy may be permissive'
                            WHEN EXISTS (
                                SELECT 1 FROM pg_inherits i
                                WHERE c.oid IN (i.inhrelid, i.inhparent)
                            ) THEN
                                'workspace tables cannot take part in '
                                'inheritance or partitioning'
                        END AS reason
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    CROSS JOIN LATERAL (
                        SELECT n.nspname LIKE 'tables\_%'
                            OR n.nspname LIKE 'custom\_fields\_%' AS dynamic
                    ) s
                    CROSS JOIN LATERAL (
                        SELECT
                            count(*) AS policies,
                            count(*) FILTER (WHERE p.polpermissive) AS permissive,
                            coalesce(bool_or(
                                p.polname = 'rls_policy_dynamic_workspace'
                            ), false) AS workspace_policy
                        FROM pg_policy p
                        WHERE p.polrelid = c.oid
                    ) pol
                    WHERE (c.oid = target
                           OR c.oid IN (SELECT i.inhrelid FROM pg_inherits i
                                        WHERE i.inhparent = target)
                           OR c.oid IN (SELECT i.inhparent FROM pg_inherits i
                                        WHERE i.inhrelid = target))
                      AND c.relkind IN ('r', 'p')
                      AND ((s.dynamic AND pol.policies > 0)
                           OR (NOT s.dynamic AND pol.workspace_policy))
                ) t
                WHERE t.reason IS NOT NULL
                LIMIT 1;
            END IF;
            EXIT WHEN problem IS NOT NULL;
        END LOOP;
    END IF;

    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'tracecat dynamic RLS guard: %', problem
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Only the role that owns this event trigger, or a '
                         'member of it, can weaken RLS on workspace tables.';
    END IF;
END;
$guard$
"""

GUARD_STATEMENTS: tuple[str, ...] = (
    _GUARD_FUNCTION_SQL,
    f"CREATE EVENT TRIGGER {DDL_TRIGGER} ON ddl_command_end "
    f"EXECUTE FUNCTION {GUARD_FUNCTION}",
    f"CREATE EVENT TRIGGER {DROP_TRIGGER} ON sql_drop "
    f"EXECUTE FUNCTION {GUARD_FUNCTION}",
)
"""Install the guard. Run in order, as a superuser (`rds_superuser` on RDS)."""

UNINSTALL_STATEMENTS: tuple[str, ...] = (
    f"DROP EVENT TRIGGER IF EXISTS {DDL_TRIGGER}",
    f"DROP EVENT TRIGGER IF EXISTS {DROP_TRIGGER}",
    f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}",
)

MANUAL_INSTALL_SQL = ";\n".join(GUARD_STATEMENTS) + ";\n"
"""The install statements as one script, for an admin to run by hand."""

AUDIT_SQL = r"""
SELECT c.oid::regclass AS dynamic_table,
       c.relrowsecurity AS enabled,
       c.relforcerowsecurity AS forced,
       array_agg(p.polname || CASE WHEN p.polpermissive THEN '' ELSE ' (restrictive)' END
                 || ': ' || coalesce(pg_get_expr(p.polqual, p.polrelid), '-'))
           FILTER (WHERE p.oid IS NOT NULL) AS policies
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_policy p ON p.polrelid = c.oid
WHERE c.relkind IN ('r', 'p')
  AND (n.nspname LIKE 'tables\_%' OR n.nspname LIKE 'custom\_fields\_%')
GROUP BY c.oid, c.relrowsecurity, c.relforcerowsecurity
HAVING NOT (c.relrowsecurity AND c.relforcerowsecurity)
    OR count(*) FILTER (WHERE p.polname = 'rls_policy_dynamic_workspace') <> 1
    OR count(*) FILTER (WHERE p.polpermissive) <> 1
"""
"""Dynamic tables whose RLS differs from what the app creates. Expect no rows.

The guard blocks new weakening DDL but doesn't repair earlier changes. Also
compare the listed policy expressions with the one in
`tracecat.tables.service`.
"""


def _protect_dynamic_tables() -> None:
    """Enable and force RLS on dynamic tables that have RLS or a policy."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            r"""
            SELECT format('%I.%I', n.nspname, c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p')
              AND (n.nspname LIKE 'tables\_%' OR n.nspname LIKE 'custom\_fields\_%')
              AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
              AND (c.relrowsecurity
                   OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
            ORDER BY 1
            """
        )
    )
    for (qualified_table,) in rows.tuples():
        op.execute(
            sa.text(
                f"ALTER TABLE {qualified_table} "
                "ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"
            )
        )


def upgrade() -> None:
    _protect_dynamic_tables()
    bind = op.get_bind()
    # Start clean, so a guard function some other role created can't keep its
    # owner (CREATE OR REPLACE keeps it). Only CREATE EVENT TRIGGER may fail
    # softly; failing to drop or create the function aborts the upgrade.
    for statement in UNINSTALL_STATEMENTS:
        bind.execute(sa.text(statement))
    bind.execute(sa.text(_GUARD_FUNCTION_SQL))
    try:
        with bind.begin_nested():
            for statement in GUARD_STATEMENTS[1:]:
                bind.execute(sa.text(statement))
    except DBAPIError as exc:
        if not isinstance(exc.orig, psycopg.errors.InsufficientPrivilege):
            raise
        bind.execute(sa.text(f"DROP FUNCTION {GUARD_FUNCTION}"))
        role = bind.execute(sa.text("SELECT session_user")).scalar_one()
        logger.warning(
            "Skipped the dynamic-table RLS guard (revision %s): role %r can't "
            "create event triggers, which needs a superuser (rds_superuser on "
            "RDS). Until an admin installs it, the app role can switch RLS off "
            "on tables_* and custom_fields_* tables. Install it before setting "
            "TRACECAT__RLS_MODE=enforce: run GUARD_STATEMENTS from this "
            "migration as a superuser. Error: %s",
            revision,
            role,
            exc.orig,
        )


def downgrade() -> None:
    bind = op.get_bind()
    for statement in UNINSTALL_STATEMENTS:
        bind.execute(sa.text(statement))
