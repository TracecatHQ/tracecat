"""Tests for the dynamic workspace RLS guard (b4074a0896b5).

Invariants:
- The app login role, a non-superuser member of the dynamic owner role, can
  switch RLS off on a dynamic table before the upgrade (DISABLE / NO FORCE,
  DROP or ALTER POLICY, an extra permissive policy, inheritance, moving the
  table out of its workspace schema). After the upgrade each of these fails,
  including through `SET ROLE` to the owner role and dynamic SQL.
- The app's runtime DDL on dynamic tables still works after the upgrade.
- Superusers and members of the trigger owner (the migration role) keep full
  DDL rights, including b0debed4e0c6's FORCE / NO FORCE.
- The upgrade enables and forces RLS on dynamic tables that have RLS or a
  policy but aren't both; downgrade removes the guard and leaves the flags.
- A migration role that can't create event triggers skips the guard with a
  warning and leaves nothing behind. A guard function another role created
  first is replaced, so that role can't keep control of it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg.errors
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from alembic import command
from tests.database import TEST_DB_CONFIG
from tracecat.identifiers.workflow import WorkspaceUUID

MIGRATION_REVISION = "b4074a0896b5"
GUARD_FUNCTION = "public.tracecat_guard_dynamic_rls()"
PREVIOUS_REVISION = "ef408b9720a6"
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"
TABLE_NAME = "probe"
STRAGGLER_TABLE = "straggler"
DISABLED_TABLE = "disabled"
POLICY = "rls_policy_dynamic_workspace"
POLICY_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    'OR "__tc_workspace_id" = '
    "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
)


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


@dataclass(frozen=True, slots=True)
class GuardDb:
    db_url: str
    app_url: str
    admin_url: str
    migrator_url: str
    owner: str
    tables_schema: str
    fields_schema: str
    moved_schema: str

    @property
    def table(self) -> str:
        return f'"{self.tables_schema}"."{TABLE_NAME}"'

    @property
    def moved_table(self) -> str:
        return f'"{self.moved_schema}"."{TABLE_NAME}"'


def _url_for(db_url: str, user: str, password: str) -> str:
    return (
        make_url(db_url)
        .set(username=user, password=password)
        .render_as_string(hide_password=False)
    )


def _alembic(
    monkeypatch: pytest.MonkeyPatch, db_url: str, action: str, revision: str
) -> None:
    monkeypatch.setenv("TRACECAT__DB_URI", db_url)
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ALEMBIC_DIR))
    match action:
        case "upgrade":
            command.upgrade(alembic_config, revision)
        case "downgrade":
            command.downgrade(alembic_config, revision)
        case _:
            raise ValueError(action)


def _run(url: str, statements: Sequence[str]) -> None:
    """Run statements in one transaction as the URL's user, then roll back."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                for statement in statements:
                    conn.execute(text(statement))
            finally:
                tx.rollback()
    finally:
        engine.dispose()


def _run_committed(url: str, statements: Sequence[str]) -> None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()


def _query(url: str, sql: str) -> list[tuple[object, ...]]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(sql))]
    finally:
        engine.dispose()


def _assert_guard_rejects(url: str, statements: Sequence[str]) -> None:
    with pytest.raises(DBAPIError) as exc_info:
        _run(url, statements)
    assert isinstance(exc_info.value.orig, psycopg.errors.InsufficientPrivilege)
    assert "tracecat dynamic RLS guard" in str(exc_info.value.orig)


def _blocked(db: GuardDb, case: str) -> list[str]:
    """Statements that weaken RLS on the probe table."""
    t, s = db.table, db.tables_schema
    escape = f"escape_{uuid.uuid4().hex[:8]}"
    match case:
        case "disable":
            return [f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY"]
        case "no_force":
            return [f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY"]
        case "disable_custom_fields":
            return [
                f'ALTER TABLE "{db.fields_schema}"."{TABLE_NAME}" '
                "DISABLE ROW LEVEL SECURITY"
            ]
        case "drop_policy":
            return [f"DROP POLICY {POLICY} ON {t}"]
        case "drop_policy_custom_fields":
            return [f'DROP POLICY {POLICY} ON "{db.fields_schema}"."{TABLE_NAME}"']
        case "drop_tenant_column_cascade":
            return [f'ALTER TABLE {t} DROP COLUMN "__tc_workspace_id" CASCADE']
        case "alter_policy_using":
            return [f"ALTER POLICY {POLICY} ON {t} USING (true) WITH CHECK (true)"]
        case "alter_policy_rename":
            return [f"ALTER POLICY {POLICY} ON {t} RENAME TO renamed"]
        case "extra_permissive_policy":
            return [f"CREATE POLICY extra ON {t} USING (true)"]
        case "set_schema":
            return [
                f'CREATE SCHEMA "{escape}"',
                f'ALTER TABLE {t} SET SCHEMA "{escape}"',
            ]
        case "rename_schema":
            return [f'ALTER SCHEMA "{s}" RENAME TO "{escape}"']
        case "inherit":
            return [
                f'CREATE TABLE "{s}".parent (id UUID)',
                f'ALTER TABLE {t} INHERIT "{s}".parent',
            ]
        case "attach_partition":
            return [
                f'CREATE TABLE "{s}".parted (LIKE {t}) '
                'PARTITION BY LIST ("__tc_workspace_id")',
                f'ALTER TABLE "{s}".parted ATTACH PARTITION {t} DEFAULT',
            ]
        case "set_role_owner":
            return [
                f'SET LOCAL ROLE "{db.owner}"',
                f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
            ]
        case "dynamic_sql":
            return [
                "DO $$ BEGIN EXECUTE format("
                f"'ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', '{t}'); END $$"
            ]
        case "search_path_shadowing":
            # A pg_has_role that always says yes, ahead of pg_catalog.
            return [
                f'CREATE SCHEMA "{escape}"',
                f'CREATE FUNCTION "{escape}".pg_has_role(name, oid, text) '
                "RETURNS boolean LANGUAGE sql AS 'SELECT true'",
                f'SET LOCAL search_path = "{escape}", pg_catalog',
                f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
            ]
        case "inherited_by_new_table":
            return [f'CREATE TABLE "{s}".child () INHERITS ({t})']
        case "inherited_by_outside_table":
            return [
                f'CREATE SCHEMA "{escape}"',
                f'CREATE TABLE "{escape}".sneaky (LIKE {t})',
                f'ALTER TABLE "{escape}".sneaky INHERIT {t}',
            ]
        case "disable_moved_table":
            return [f"ALTER TABLE {db.moved_table} DISABLE ROW LEVEL SECURITY"]
        case "drop_policy_moved_table":
            return [f"DROP POLICY {POLICY} ON {db.moved_table}"]
        case _:
            raise ValueError(case)


BLOCKED_CASES = (
    "disable",
    "no_force",
    "disable_custom_fields",
    "drop_policy",
    "drop_policy_custom_fields",
    "drop_tenant_column_cascade",
    "alter_policy_using",
    "alter_policy_rename",
    "extra_permissive_policy",
    "set_schema",
    "rename_schema",
    "inherit",
    "attach_partition",
    "set_role_owner",
    "dynamic_sql",
    "search_path_shadowing",
    "inherited_by_new_table",
    "inherited_by_outside_table",
    "disable_moved_table",
    "drop_policy_moved_table",
)


def _app_runtime_ddl(db: GuardDb) -> list[str]:
    """The DDL TablesService and CaseFieldsService run as the app role."""
    short = WorkspaceUUID.new(uuid.uuid4()).short()
    s = f"tables_{short}"
    t = f'"{s}"."alerts"'
    return [
        # create_table
        f'CREATE SCHEMA IF NOT EXISTS "{s}"',
        f'ALTER SCHEMA "{s}" OWNER TO "{db.owner}"',
        f'CREATE TABLE {t} (id UUID PRIMARY KEY, "__tc_workspace_id" UUID NOT NULL)',
        f'ALTER TABLE {t} OWNER TO "{db.owner}"',
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY} ON {t}",
        f"CREATE POLICY {POLICY} ON {t} FOR ALL "
        f"USING ({POLICY_EXPR}) WITH CHECK ({POLICY_EXPR})",
        # columns and indexes
        f"ALTER TABLE {t} ADD COLUMN name TEXT, ADD COLUMN level TEXT",
        f"ALTER TABLE {t} RENAME COLUMN level TO severity",
        f"ALTER TABLE {t} ALTER COLUMN severity TYPE INTEGER USING NULL",
        f"ALTER TABLE {t} ALTER COLUMN name SET DEFAULT 'x'",
        f"ALTER TABLE {t} DROP COLUMN severity",
        f"CREATE UNIQUE INDEX ON {t} (name)",
        # A restrictive policy only narrows access.
        f"CREATE POLICY narrow ON {t} AS RESTRICTIVE USING (true)",
        # Re-applying ENABLE / FORCE to an existing, protected table.
        f"ALTER TABLE {db.table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {db.table} FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {db.table} ADD COLUMN note TEXT",
        # delete_table, drop_workspace_schema
        f"DROP TABLE {t}",
        f'DROP SCHEMA "{db.fields_schema}" CASCADE',
        f'DROP SCHEMA "{s}" CASCADE',
    ]


def _guard_objects(url: str) -> list[tuple[object, ...]]:
    return _query(
        url,
        """
        SELECT evtname::text FROM pg_event_trigger
        WHERE evtname LIKE 'tracecat_guard_dynamic_rls%'
        UNION ALL
        SELECT proname::text FROM pg_proc WHERE proname = 'tracecat_guard_dynamic_rls'
        ORDER BY 1
        """,
    )


def _rls_flags(url: str, schema_name: str, table_name: str) -> tuple[bool, bool]:
    """Return (enabled, forced) for a table."""
    rows = _query(
        url,
        f"""
        SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{schema_name}' AND c.relname = '{table_name}'
        """,
    )
    return bool(rows[0][0]), bool(rows[0][1])


def _stragglers(db: GuardDb) -> list[tuple[str, str]]:
    return [
        (db.tables_schema, STRAGGLER_TABLE),
        (db.fields_schema, STRAGGLER_TABLE),
        (db.tables_schema, DISABLED_TABLE),
    ]


@pytest.fixture
def guard_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[GuardDb]:
    """A database at the previous revision with staging's role layout.

    `app` logs in, is no superuser and has no BYPASSRLS, and is a member of
    the NOLOGIN owner role (INHERIT, SET) that owns both workspace schemas and
    their tables. `admin` is a non-superuser member of the migration role
    (here the bootstrap superuser) and of the owner role, like the RDS master
    on staging.
    `migrator` is a non-superuser that can run migrations but not create
    event triggers.

    Stragglers: one table per workspace schema with RLS enabled but not
    forced, and one with a policy but RLS disabled. `moved_*` holds a fully
    protected workspace table outside the workspace schema names, as if it
    had been moved there before the guard existed.
    """
    sys_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    suffix = uuid.uuid4().hex[:8]
    db_name = f"test_rls_guard_{suffix}"
    owner = f"rls_guard_owner_{suffix}"
    app = f"rls_guard_app_{suffix}"
    admin = f"rls_guard_admin_{suffix}"
    migrator = f"rls_guard_migrator_{suffix}"
    password = uuid.uuid4().hex
    short = WorkspaceUUID.new(uuid.uuid4()).short()
    tables_schema, fields_schema = f"tables_{short}", f"custom_fields_{short}"
    moved_schema = f"moved_{suffix}"
    terminate = text(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = :db_name AND pid <> pg_backend_pid()"
    )

    try:
        with sys_engine.connect() as conn:
            migration_role = conn.execute(text("SELECT current_user")).scalar_one()
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            conn.execute(text(f'CREATE ROLE "{owner}" NOLOGIN'))
            for login in (app, admin, migrator):
                conn.execute(
                    text(
                        f'CREATE ROLE "{login}" LOGIN NOSUPERUSER NOBYPASSRLS '
                        f"PASSWORD '{password}'"
                    )
                )
            conn.execute(
                text(f'GRANT "{owner}" TO "{app}" WITH INHERIT TRUE, SET TRUE')
            )
            conn.execute(text(f'GRANT "{migration_role}" TO "{admin}"'))
            conn.execute(
                text(f'GRANT "{owner}" TO "{admin}" WITH INHERIT TRUE, SET TRUE')
            )

        db_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, db_name
        )
        engine = create_engine(db_url, poolclass=NullPool)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(f'GRANT CREATE ON DATABASE "{db_name}" TO "{app}", "{owner}"')
                )
                conn.execute(text(f'GRANT CREATE ON SCHEMA public TO "{migrator}"'))
                # (schema, table, enable, force)
                tables = [
                    (tables_schema, TABLE_NAME, True, True),
                    (fields_schema, TABLE_NAME, True, True),
                    (moved_schema, TABLE_NAME, True, True),
                    (tables_schema, STRAGGLER_TABLE, True, False),
                    (fields_schema, STRAGGLER_TABLE, True, False),
                    (tables_schema, DISABLED_TABLE, False, False),
                ]
                for schema_name in (tables_schema, fields_schema, moved_schema):
                    conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                    conn.execute(
                        text(f'ALTER SCHEMA "{schema_name}" OWNER TO "{owner}"')
                    )
                for schema_name, table_name, enable, force in tables:
                    qualified = f'"{schema_name}"."{table_name}"'
                    conn.execute(
                        text(
                            f"CREATE TABLE {qualified} (id UUID PRIMARY KEY, "
                            '"__tc_workspace_id" UUID NOT NULL)'
                        )
                    )
                    if enable:
                        conn.execute(
                            text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
                        )
                    if force:
                        conn.execute(
                            text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
                        )
                    conn.execute(
                        text(
                            f"CREATE POLICY {POLICY} ON {qualified} FOR ALL "
                            f"USING ({POLICY_EXPR}) WITH CHECK ({POLICY_EXPR})"
                        )
                    )
                    conn.execute(text(f'ALTER TABLE {qualified} OWNER TO "{owner}"'))
        finally:
            engine.dispose()

        command_config = Config()
        command_config.set_main_option("script_location", str(ALEMBIC_DIR))
        monkeypatch.setenv("TRACECAT__DB_URI", db_url)
        command.stamp(command_config, PREVIOUS_REVISION)
        engine = create_engine(db_url, poolclass=NullPool)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(f'GRANT SELECT, UPDATE ON alembic_version TO "{migrator}"')
                )
        finally:
            engine.dispose()

        yield GuardDb(
            db_url=db_url,
            app_url=_url_for(db_url, app, password),
            admin_url=_url_for(db_url, admin, password),
            migrator_url=_url_for(db_url, migrator, password),
            owner=owner,
            tables_schema=tables_schema,
            fields_schema=fields_schema,
            moved_schema=moved_schema,
        )
    finally:
        with sys_engine.connect() as conn:
            conn.execute(terminate, {"db_name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            for role in (app, admin, migrator, owner):
                conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        sys_engine.dispose()


class TestDynamicRlsGuardMigration:
    @pytest.mark.parametrize("case", BLOCKED_CASES)
    def test_app_can_weaken_rls_before_and_not_after(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        # Fails on base: without the guard the app role weakens RLS freely.
        _run(guard_db.app_url, _blocked(guard_db, case))

        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, case))

    def test_app_runtime_ddl_still_works(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        _run(guard_db.app_url, _app_runtime_ddl(guard_db))

    @pytest.mark.parametrize("who", ["superuser", "member_of_migration_role"])
    def test_privileged_roles_keep_full_ddl(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch, who: str
    ) -> None:
        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)
        url = guard_db.db_url if who == "superuser" else guard_db.admin_url

        for case in BLOCKED_CASES:
            _run(url, _blocked(guard_db, case))
        # b0debed4e0c6's downgrade and upgrade statements.
        _run(
            url,
            [
                f"ALTER TABLE {guard_db.table} NO FORCE ROW LEVEL SECURITY",
                f"ALTER TABLE {guard_db.table} FORCE ROW LEVEL SECURITY",
            ],
        )

    def test_upgrade_protects_stragglers_and_downgrade_removes_guard(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = [_rls_flags(guard_db.db_url, *t) for t in _stragglers(guard_db)]
        assert before == [(True, False), (True, False), (False, False)]

        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        after = [_rls_flags(guard_db.db_url, *t) for t in _stragglers(guard_db)]
        assert after == [(True, True)] * 3
        assert _guard_objects(guard_db.db_url) == [
            ("tracecat_guard_dynamic_rls",),
            ("tracecat_guard_dynamic_rls_ddl",),
            ("tracecat_guard_dynamic_rls_drop",),
        ]

        _alembic(monkeypatch, guard_db.db_url, "downgrade", PREVIOUS_REVISION)

        assert _guard_objects(guard_db.db_url) == []
        after = [_rls_flags(guard_db.db_url, *t) for t in _stragglers(guard_db)]
        assert after == [(True, True)] * 3
        _run(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_upgrade_replaces_a_guard_function_another_role_created(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # CREATE OR REPLACE would keep this owner, who could then rewrite the
        # function to let everything through.
        _run_committed(
            guard_db.migrator_url,
            [
                f"CREATE FUNCTION {GUARD_FUNCTION} RETURNS event_trigger "
                "LANGUAGE plpgsql AS $$ BEGIN END $$"
            ],
        )

        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        superuser = _query(guard_db.db_url, "SELECT current_user")[0][0]
        assert _query(
            guard_db.db_url,
            "SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE proname = 'tracecat_guard_dynamic_rls'",
        ) == [(superuser,)]
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_non_superuser_migration_skips_guard(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The migrator doesn't own the stragglers; protect them so the
        # upgrade's backfill has nothing to do.
        _run_committed(
            guard_db.db_url,
            [
                f'ALTER TABLE "{schema}"."{table}" '
                "ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"
                for schema, table in _stragglers(guard_db)
            ],
        )

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _alembic(monkeypatch, guard_db.migrator_url, "upgrade", MIGRATION_REVISION)

        assert "Skipped the dynamic-table RLS guard" in caplog.text
        assert _query(guard_db.db_url, "SELECT version_num FROM alembic_version") == [
            (MIGRATION_REVISION,)
        ]
        # The savepoint rolled back the function too.
        assert _guard_objects(guard_db.db_url) == []
        _run(guard_db.app_url, _blocked(guard_db, "disable"))
