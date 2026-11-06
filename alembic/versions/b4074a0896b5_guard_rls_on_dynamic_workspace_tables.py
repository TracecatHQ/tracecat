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
- moving a workspace table (one with the workspace policy or the
  `__tc_workspace_id` column) out of a workspace schema, by
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
  workspace tables whose RLS differs from what the app creates.

`CREATE EVENT TRIGGER` needs a superuser (`rds_superuser` on RDS). When the
migration role can't create it, the upgrade logs a warning and skips the
guard rather than failing: the guard only matters with
`TRACECAT__RLS_MODE=enforce`. An admin can install it later by running
`MANUAL_INSTALL_SQL` as a superuser against the Tracecat database. Run
migrations as that admin, or as a member of it, so they keep full DDL rights
on dynamic tables.

The upgrade keeps a guard that is already installed (both triggers enabled
and owned by the function's owner). Otherwise it drops any partial guard and
installs a fresh one; a stray guard function owned by a role it can't drop
fails the upgrade with instructions. After installing, it runs `AUDIT_SQL`
and logs a warning for each workspace table whose RLS differs from what the
app creates.

Managed Postgres blocks major version upgrades while event triggers exist
(RDS and Azure Flexible Server both say to drop them first). Drop the guard
with the statements in `UNINSTALL_STATEMENTS` before a major upgrade, and run
`MANUAL_INSTALL_SQL` again afterwards.

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
                      WHERE c.relnamespace = n.oid
                        AND (EXISTS (SELECT 1 FROM pg_policy p
                                     WHERE p.polrelid = c.oid
                                       AND p.polname = 'rls_policy_dynamic_workspace')
                             OR EXISTS (SELECT 1 FROM pg_attribute a
                                        WHERE a.attrelid = c.oid
                                          AND a.attname = '__tc_workspace_id'
                                          AND NOT a.attisdropped))
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
                    CROSS JOIN LATERAL (
                        SELECT EXISTS (
                            SELECT 1 FROM pg_attribute a
                            WHERE a.attrelid = c.oid
                              AND a.attname = '__tc_workspace_id'
                              AND NOT a.attisdropped
                        ) AS tenant_column
                    ) col
                    WHERE (c.oid = target
                           OR c.oid IN (SELECT i.inhrelid FROM pg_inherits i
                                        WHERE i.inhparent = target)
                           OR c.oid IN (SELECT i.inhparent FROM pg_inherits i
                                        WHERE i.inhrelid = target))
                      AND c.relkind IN ('r', 'p')
                      AND ((s.dynamic AND pol.policies > 0)
                           -- Workspace tables outside workspace schemas, found
                           -- by policy name or tenant column, so renaming one
                           -- of the two doesn't let a table out.
                           OR (NOT s.dynamic AND c.relpersistence <> 't'
                               AND (pol.workspace_policy OR col.tenant_column)))
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
"""The guard function and its two event triggers, in install order."""

UNINSTALL_STATEMENTS: tuple[str, ...] = (
    f"DROP EVENT TRIGGER IF EXISTS {DDL_TRIGGER}",
    f"DROP EVENT TRIGGER IF EXISTS {DROP_TRIGGER}",
    f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}",
)

MANUAL_INSTALL_SQL = ";\n".join((*UNINSTALL_STATEMENTS, *GUARD_STATEMENTS)) + ";\n"
"""Drop and reinstall the guard, for an admin to run by hand as a superuser.

Dropping first matters: `CREATE OR REPLACE FUNCTION` keeps an existing
function's owner, who could later rewrite it.
"""

# pg_get_expr's rendering of the policy expression in tracecat.tables.service.
_WORKSPACE_POLICY_EXPR = (
    "((current_setting('app.rls_bypass'::text, true) = 'on'::text) OR "
    "(__tc_workspace_id = (NULLIF(current_setting("
    "'app.current_workspace_id'::text, true), ''::text))::uuid))"
)

AUDIT_SQL = rf"""
WITH workspace_tables AS (
    SELECT c.oid, c.relrowsecurity, c.relforcerowsecurity,
           n.nspname LIKE 'tables\_%' OR n.nspname LIKE 'custom\_fields\_%'
               AS in_workspace_schema
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p')
      AND c.relpersistence <> 't'
      AND (n.nspname LIKE 'tables\_%'
           OR n.nspname LIKE 'custom\_fields\_%'
           OR EXISTS (SELECT 1 FROM pg_policy p
                      WHERE p.polrelid = c.oid
                        AND p.polname = 'rls_policy_dynamic_workspace')
           OR EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid
                        AND a.attname = '__tc_workspace_id'
                        AND NOT a.attisdropped))
)
SELECT t.workspace_table, t.problems
FROM (
    SELECT w.oid::regclass::text AS workspace_table,
           array_remove(ARRAY[
               CASE WHEN NOT w.in_workspace_schema
                    THEN 'outside a workspace schema' END,
               CASE WHEN NOT w.relrowsecurity THEN 'RLS disabled' END,
               CASE WHEN NOT w.relforcerowsecurity THEN 'RLS not forced' END,
               CASE WHEN wp.oid IS NULL THEN 'no workspace policy' END,
               CASE WHEN wp.oid IS NOT NULL AND NOT (
                        wp.polpermissive
                        AND wp.polcmd = '*'
                        AND wp.polroles = '{{0}}'::oid[]
                        -- A missing USING or WITH CHECK is NULL, which = would
                        -- turn into "no problem".
                        AND pg_get_expr(wp.polqual, wp.polrelid)
                            IS NOT DISTINCT FROM
                            '{_WORKSPACE_POLICY_EXPR.replace("'", "''")}'
                        AND pg_get_expr(wp.polwithcheck, wp.polrelid)
                            IS NOT DISTINCT FROM
                            '{_WORKSPACE_POLICY_EXPR.replace("'", "''")}')
                    THEN 'workspace policy differs from the app''s' END,
               CASE WHEN EXISTS (SELECT 1 FROM pg_policy o
                                 WHERE o.polrelid = w.oid AND o.polpermissive
                                   AND o.polname <> 'rls_policy_dynamic_workspace')
                    THEN 'extra permissive policy' END,
               CASE WHEN EXISTS (SELECT 1 FROM pg_inherits i
                                 WHERE w.oid IN (i.inhrelid, i.inhparent))
                    THEN 'inheritance or partitioning' END
           ], NULL) AS problems
    FROM workspace_tables w
    LEFT JOIN pg_policy wp
      ON wp.polrelid = w.oid AND wp.polname = 'rls_policy_dynamic_workspace'
) t
WHERE cardinality(t.problems) > 0
ORDER BY 1
"""
"""Workspace tables whose RLS differs from what the app creates. Expect no rows.

The upgrade runs it and logs a warning per problem table. The guard blocks
new weakening DDL but doesn't repair earlier changes; fix them as the
migration role.
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


def _guard_installed() -> bool:
    """Both triggers are enabled and call this revision's guard, with one owner.

    An older or edited body counts as not installed, so it gets replaced.
    """
    installed = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT count(*) = 2
            FROM pg_event_trigger t
            JOIN pg_proc f ON f.oid = t.evtfoid
            WHERE t.evtname IN (:ddl_trigger, :drop_trigger)
              AND t.evtenabled <> 'D'
              AND f.oid = to_regprocedure(:function)
              AND f.proowner = t.evtowner
              AND f.prosrc = :body
            """
            ),
            {
                "ddl_trigger": DDL_TRIGGER,
                "drop_trigger": DROP_TRIGGER,
                "function": GUARD_FUNCTION,
                "body": _GUARD_FUNCTION_SQL.split("$guard$")[1],
            },
        )
        .scalar_one()
    )
    return bool(installed)


def _assert_guard_function_droppable() -> None:
    """Fail clearly if another role owns a function with the guard's name."""
    owner = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT pg_get_userbyid(proowner) FROM pg_proc
                WHERE oid = to_regprocedure(:function)
                  AND NOT pg_has_role(current_user, proowner, 'MEMBER')
                """
            ),
            {"function": GUARD_FUNCTION},
        )
        .scalar_one_or_none()
    )
    if owner is not None:
        raise RuntimeError(
            f"{GUARD_FUNCTION} already exists and is owned by {owner!r}, which "
            "this migration role can't drop. That role could rewrite the "
            "guard. Drop the function as a superuser or as its owner, then "
            "rerun the upgrade."
        )


def _install_guard() -> None:
    bind = op.get_bind()
    if _guard_installed():
        logger.info("Dynamic-table RLS guard is already installed; keeping it.")
        return
    _assert_guard_function_droppable()
    # Start clean: CREATE OR REPLACE would keep a stray function's owner. Only
    # CREATE EVENT TRIGGER may fail softly.
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
            "TRACECAT__RLS_MODE=enforce: run MANUAL_INSTALL_SQL from this "
            "migration as a superuser. Error: %s",
            revision,
            role,
            exc.orig,
        )


def _log_rls_drift() -> None:
    rows = op.get_bind().execute(sa.text(AUDIT_SQL)).tuples().all()
    for workspace_table, problems in rows:
        logger.warning(
            "Workspace table %s has RLS drift: %s. Fix it as the migration "
            "role; the guard keeps the app role from changing it.",
            workspace_table,
            ", ".join(problems),
        )


def upgrade() -> None:
    _protect_dynamic_tables()
    _install_guard()
    _log_rls_drift()


def downgrade() -> None:
    bind = op.get_bind()
    for statement in UNINSTALL_STATEMENTS:
        bind.execute(sa.text(statement))
