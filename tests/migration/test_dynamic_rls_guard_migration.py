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
  warning and leaves nothing behind, and keeps a guard an admin already
  installed. A guard function another role created first is replaced, or the
  upgrade fails with instructions when the migration role can't drop it. An
  outdated, replica-only or tag-filtered guard is replaced, or kept when the migration role
  can't recreate event triggers.
- The upgrade logs a warning for each workspace table whose RLS differs from
  what the app creates, and a policy renamed before the upgrade doesn't let
  its table leave the workspace schema afterwards.

4ea30f9ea491 (exempt the guard function's owner), in the shape RDS leaves
b4074a0896b5's guard: a superuser owns the triggers (`rdsadmin` on RDS) and a
non-superuser migration login that isn't a member of it owns the function:
- Before the upgrade the guard binds the migration login; after it, the
  migration login and members of the function's owner can disable, un-force,
  drop policies and re-own drifted dynamic tables.
- The app is still rejected for every blocked case, can't `SET ROLE` to the
  function's owner, and can't replace, re-own or unhook the guard.
- The upgrade only replaces the body (same function and triggers), a rerun
  changes nothing, and downgrade restores b4074a0896b5's body.
- It keeps a body it can't replace, warns when no guard fires, and keeps the
  exemption for members of the owner where one role owns everything.
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

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
OWNER_EXEMPTION_REVISION = "4ea30f9ea491"
GUARD_FUNCTION = "public.tracecat_guard_dynamic_rls()"
PREVIOUS_REVISION = "d986afa25989"
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
        case "stamp":
            command.stamp(alembic_config, revision)
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


GUARD_REVISION_FILES = {
    MIGRATION_REVISION: f"{MIGRATION_REVISION}_guard_rls_on_dynamic_workspace_tables.py",
    OWNER_EXEMPTION_REVISION: (
        f"{OWNER_EXEMPTION_REVISION}_exempt_dynamic_rls_guard_function_owner.py"
    ),
}


def _load_revision(revision: str) -> ModuleType:
    path = ALEMBIC_DIR / "versions" / GUARD_REVISION_FILES[revision]
    spec = importlib.util.spec_from_file_location(f"guard_{revision}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_statements(revision: str = MIGRATION_REVISION) -> list[str]:
    """MANUAL_INSTALL_SQL from a guard migration, as separate statements."""
    module = _load_revision(revision)
    return [*module.UNINSTALL_STATEMENTS, *module.GUARD_STATEMENTS]


def _revision_body(revision: str) -> str:
    """The guard function body a revision installs."""
    return _load_revision(revision)._GUARD_FUNCTION_SQL.split("$guard$")[1]


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

    def test_upgrade_replaces_an_outdated_guard_body(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An older install whose function lets everything through.
        _run_committed(
            guard_db.db_url,
            [
                *_install_statements(),
                f"CREATE OR REPLACE FUNCTION {GUARD_FUNCTION} RETURNS event_trigger "
                "LANGUAGE plpgsql AS $$ BEGIN END $$",
            ],
        )
        _run(guard_db.app_url, _blocked(guard_db, "disable"))

        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_rerun_without_privilege_keeps_an_installed_guard(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _run_committed(guard_db.db_url, _install_statements())
        _run_committed(
            guard_db.db_url,
            [
                f'ALTER TABLE "{schema}"."{table}" '
                "ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"
                for schema, table in _stragglers(guard_db)
            ],
        )

        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _alembic(monkeypatch, guard_db.migrator_url, "upgrade", MIGRATION_REVISION)

        assert "already installed" in caplog.text
        assert len(_guard_objects(guard_db.db_url)) == 3
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_upgrade_refuses_a_guard_function_it_cannot_drop(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run_committed(
            guard_db.migrator_url,
            [
                f"CREATE FUNCTION {GUARD_FUNCTION} RETURNS event_trigger "
                "LANGUAGE plpgsql AS $$ BEGIN END $$"
            ],
        )

        # admin is no superuser and not a member of the migrator role.
        with pytest.raises(RuntimeError, match="already exists and is owned by"):
            _alembic(monkeypatch, guard_db.admin_url, "upgrade", MIGRATION_REVISION)

    @pytest.mark.parametrize(
        "tamper", ["permissive_expression", "no_using_clause", "no_clauses"]
    )
    def test_upgrade_logs_rls_drift(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tamper: str,
    ) -> None:
        # Pre-guard tampering that keeps the policy's name and the RLS flags.
        t = guard_db.table
        match tamper:
            case "permissive_expression":
                statements = [
                    f"ALTER POLICY {POLICY} ON {t} USING (true) WITH CHECK (true)"
                ]
            case "no_using_clause":
                statements = [
                    f"DROP POLICY {POLICY} ON {t}",
                    f"CREATE POLICY {POLICY} ON {t} FOR ALL WITH CHECK ({POLICY_EXPR})",
                ]
            case _:
                statements = [
                    f"DROP POLICY {POLICY} ON {t}",
                    f"CREATE POLICY {POLICY} ON {t}",
                ]
        _run_committed(guard_db.app_url, statements)

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        drift = sorted(
            r.getMessage() for r in caplog.records if "RLS drift" in r.getMessage()
        )
        assert len(drift) == 2
        moved, tampered = sorted(drift, key=lambda m: guard_db.moved_schema not in m)
        assert guard_db.moved_schema in moved
        assert "outside a workspace schema" in moved
        assert guard_db.tables_schema in tampered
        assert "workspace policy differs from the app's" in tampered

    def test_policy_renamed_before_upgrade_cannot_carry_table_out(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run_committed(
            guard_db.app_url,
            [f"ALTER POLICY {POLICY} ON {guard_db.table} RENAME TO renamed"],
        )

        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "set_schema"))
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "rename_schema"))

    @pytest.mark.parametrize("drift", ["replica_only", "tag_filtered"])
    def test_upgrade_replaces_a_guard_that_misses_commands(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch, drift: str
    ) -> None:
        # Replica-only triggers never fire in normal sessions; a WHEN TAG
        # filter skips ALTER TABLE.
        match drift:
            case "replica_only":
                tweak = [
                    "ALTER EVENT TRIGGER tracecat_guard_dynamic_rls_ddl ENABLE REPLICA",
                    "ALTER EVENT TRIGGER tracecat_guard_dynamic_rls_drop ENABLE REPLICA",
                ]
            case _:
                tweak = [
                    "DROP EVENT TRIGGER tracecat_guard_dynamic_rls_ddl",
                    "CREATE EVENT TRIGGER tracecat_guard_dynamic_rls_ddl "
                    "ON ddl_command_end WHEN TAG IN ('CREATE TABLE') "
                    f"EXECUTE FUNCTION {GUARD_FUNCTION}",
                ]
        _run_committed(guard_db.db_url, [*_install_statements(), *tweak])
        _run(guard_db.app_url, _blocked(guard_db, "disable"))

        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_role_that_cannot_recreate_triggers_keeps_an_outdated_guard(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A working guard from an older revision: same logic, different body.
        statements = _install_statements()
        guard_index = len(statements) - 3
        statements[guard_index] = statements[guard_index].replace(
            "BEGIN\n", "BEGIN\n    -- older revision\n", 1
        )
        _run_committed(guard_db.db_url, statements)
        _run_committed(
            guard_db.db_url,
            [
                f'ALTER TABLE "{schema}"."{table}" '
                "ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"
                for schema, table in _stragglers(guard_db)
            ],
        )

        # admin can drop the superuser's triggers through membership, but
        # can't create event triggers.
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _alembic(monkeypatch, guard_db.admin_url, "upgrade", MIGRATION_REVISION)

        assert "Kept the existing dynamic-table RLS guard" in caplog.text
        assert len(_guard_objects(guard_db.db_url)) == 3
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_disabled_guard_that_cannot_be_reinstalled_is_reported_as_skipped(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _run_committed(
            guard_db.db_url,
            [
                *_install_statements(),
                "ALTER EVENT TRIGGER tracecat_guard_dynamic_rls_ddl DISABLE",
                *(
                    f'ALTER TABLE "{schema}"."{table}" '
                    "ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"
                    for schema, table in _stragglers(guard_db)
                ),
            ],
        )

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _alembic(monkeypatch, guard_db.admin_url, "upgrade", MIGRATION_REVISION)

        assert "Skipped the dynamic-table RLS guard" in caplog.text
        assert "Kept the existing" not in caplog.text

    def test_backfill_names_a_table_the_migration_role_cannot_alter(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = make_url(guard_db.app_url).username
        straggler = f'"{guard_db.tables_schema}"."{STRAGGLER_TABLE}"'
        _run_committed(guard_db.db_url, [f'ALTER TABLE {straggler} OWNER TO "{app}"'])

        # admin is a member of the owner role, not of the app login.
        with pytest.raises(RuntimeError, match="Can't enable and force RLS on"):
            _alembic(monkeypatch, guard_db.admin_url, "upgrade", MIGRATION_REVISION)


def _guard_body(url: str) -> object:
    return _query(
        url,
        f"SELECT prosrc FROM pg_proc WHERE oid = to_regprocedure('{GUARD_FUNCTION}')",
    )[0][0]


def _guard_identity(url: str) -> list[tuple[object, ...]]:
    """(object, oid, owner) for the guard function and both triggers."""
    return _query(
        url,
        f"""
        SELECT 'function', oid::text, pg_get_userbyid(proowner)::text
        FROM pg_proc WHERE oid = to_regprocedure('{GUARD_FUNCTION}')
        UNION ALL
        SELECT evtname::text, oid::text, pg_get_userbyid(evtowner)::text
        FROM pg_event_trigger WHERE evtname LIKE 'tracecat_guard_dynamic_rls%'
        ORDER BY 1
        """,
    )


def _guard_row_versions(url: str) -> list[tuple[object, ...]]:
    """xmin of the guard's catalog rows; it changes when a row is rewritten."""
    return _query(
        url,
        f"""
        SELECT 'function', xmin::text
        FROM pg_proc WHERE oid = to_regprocedure('{GUARD_FUNCTION}')
        UNION ALL
        SELECT evtname::text, xmin::text
        FROM pg_event_trigger WHERE evtname LIKE 'tracecat_guard_dynamic_rls%'
        ORDER BY 1
        """,
    )


def _migration_privileged(db: GuardDb, case: str) -> list[str]:
    """DDL a migration or the role bootstrap's upgrade runs on dynamic tables."""
    match case:
        case "reown_drifted_table":
            # The straggler is enabled but not forced, so the guard checks it.
            return [
                f'ALTER TABLE "{db.tables_schema}"."{STRAGGLER_TABLE}" '
                f'OWNER TO "{make_url(db.migrator_url).username}"'
            ]
        case _:
            return _blocked(db, case)


MIGRATION_PRIVILEGED_CASES = (
    "disable",
    "no_force",
    "drop_policy",
    "alter_policy_using",
    "reown_drifted_table",
)


@pytest.fixture
def rds_guard(guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch) -> GuardDb:
    """b4074a0896b5's guard in the shape RDS leaves it.

    The superuser owns both event triggers, as `rdsadmin` does on RDS. The
    function belongs to `migrator`, as it does to the RDS master that ran
    b4074a0896b5: no superuser and no member of the triggers' owner. Like
    `tracecat_owner`, `migrator` is a member of the dynamic owner role. One
    straggler is left enabled but not forced.
    """
    migrator = make_url(guard_db.migrator_url).username
    _run_committed(
        guard_db.db_url,
        [f'GRANT "{guard_db.owner}" TO "{migrator}" WITH INHERIT TRUE, SET TRUE'],
    )
    _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)
    _run_committed(
        guard_db.db_url,
        [
            f'ALTER FUNCTION {GUARD_FUNCTION} OWNER TO "{migrator}"',
            f'ALTER TABLE "{guard_db.tables_schema}"."{STRAGGLER_TABLE}" '
            "NO FORCE ROW LEVEL SECURITY",
        ],
    )
    return guard_db


class TestGuardFunctionOwnerMigration:
    @pytest.mark.parametrize("case", MIGRATION_PRIVILEGED_CASES)
    def test_migration_login_is_bound_before_and_exempt_after(
        self, rds_guard: GuardDb, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        # Fails on base: b4074a0896b5 exempts only the triggers' owner.
        _assert_guard_rejects(
            rds_guard.migrator_url, _migration_privileged(rds_guard, case)
        )

        _alembic(
            monkeypatch, rds_guard.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
        )

        _run(rds_guard.migrator_url, _migration_privileged(rds_guard, case))

    def test_member_of_function_owner_is_exempt(
        self, rds_guard: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Like the admin joining tracecat_owner under the role bootstrap.
        admin = make_url(rds_guard.admin_url).username
        migrator = make_url(rds_guard.migrator_url).username
        _run_committed(rds_guard.db_url, [f'GRANT "{migrator}" TO "{admin}"'])
        _alembic(
            monkeypatch, rds_guard.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
        )

        for case in MIGRATION_PRIVILEGED_CASES:
            _run(rds_guard.admin_url, _migration_privileged(rds_guard, case))

    @pytest.mark.parametrize("case", BLOCKED_CASES)
    def test_app_is_still_rejected(
        self, rds_guard: GuardDb, monkeypatch: pytest.MonkeyPatch, case: str
    ) -> None:
        _alembic(
            monkeypatch, rds_guard.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
        )

        _assert_guard_rejects(rds_guard.app_url, _blocked(rds_guard, case))

    @pytest.mark.parametrize(
        "attempt",
        [
            "set_role_function_owner",
            "replace_function",
            "alter_function_owner",
            "drop_trigger",
            "disable_trigger",
        ],
    )
    def test_app_cannot_take_over_the_guard(
        self, rds_guard: GuardDb, monkeypatch: pytest.MonkeyPatch, attempt: str
    ) -> None:
        _alembic(
            monkeypatch, rds_guard.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
        )
        app = make_url(rds_guard.app_url).username
        migrator = make_url(rds_guard.migrator_url).username
        # Worst case: the app may create objects in public.
        _run_committed(rds_guard.db_url, [f'GRANT CREATE ON SCHEMA public TO "{app}"'])
        match attempt:
            case "set_role_function_owner":
                statements = [
                    f'SET LOCAL ROLE "{migrator}"',
                    f"ALTER TABLE {rds_guard.table} DISABLE ROW LEVEL SECURITY",
                ]
            case "replace_function":
                statements = [
                    f"CREATE OR REPLACE FUNCTION {GUARD_FUNCTION} "
                    "RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN END $$"
                ]
            case "alter_function_owner":
                statements = [f'ALTER FUNCTION {GUARD_FUNCTION} OWNER TO "{app}"']
            case "drop_trigger":
                statements = ["DROP EVENT TRIGGER tracecat_guard_dynamic_rls_ddl"]
            case _:
                statements = [
                    "ALTER EVENT TRIGGER tracecat_guard_dynamic_rls_ddl DISABLE"
                ]

        with pytest.raises(DBAPIError) as exc_info:
            _run(rds_guard.app_url, statements)
        assert isinstance(exc_info.value.orig, psycopg.errors.InsufficientPrivilege)
        _assert_guard_rejects(rds_guard.app_url, _blocked(rds_guard, "disable"))

    def test_upgrade_replaces_only_the_body_and_rerun_is_a_no_op(
        self,
        rds_guard: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        identity = _guard_identity(rds_guard.db_url)
        trigger_versions = _guard_row_versions(rds_guard.db_url)[1:]

        _alembic(
            monkeypatch, rds_guard.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
        )

        assert _guard_body(rds_guard.db_url) == _revision_body(OWNER_EXEMPTION_REVISION)
        assert _guard_identity(rds_guard.db_url) == identity
        assert _guard_row_versions(rds_guard.db_url)[1:] == trigger_versions

        versions = _guard_row_versions(rds_guard.db_url)
        _alembic(monkeypatch, rds_guard.db_url, "stamp", MIGRATION_REVISION)
        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _alembic(
                monkeypatch,
                rds_guard.migrator_url,
                "upgrade",
                OWNER_EXEMPTION_REVISION,
            )

        assert "already has this body" in caplog.text
        assert _guard_row_versions(rds_guard.db_url) == versions

    def test_downgrade_restores_the_previous_body(
        self, rds_guard: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        identity = _guard_identity(rds_guard.db_url)
        _alembic(
            monkeypatch, rds_guard.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
        )

        _alembic(monkeypatch, rds_guard.migrator_url, "downgrade", MIGRATION_REVISION)

        assert _guard_body(rds_guard.db_url) == _revision_body(MIGRATION_REVISION)
        assert _guard_identity(rds_guard.db_url) == identity
        _assert_guard_rejects(rds_guard.migrator_url, _blocked(rds_guard, "disable"))

    def test_upgrade_keeps_a_body_it_cannot_replace(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The superuser installed the guard and owns the function.
        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _alembic(
                monkeypatch, guard_db.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
            )

        assert "Kept the dynamic-table RLS guard's body" in caplog.text
        assert _guard_body(guard_db.db_url) == _revision_body(MIGRATION_REVISION)
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_upgrade_refuses_a_rewritten_body_it_cannot_replace(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The superuser's guard, rewritten to let everything through.
        _alembic(monkeypatch, guard_db.db_url, "upgrade", MIGRATION_REVISION)
        _run_committed(
            guard_db.db_url,
            [
                f"CREATE OR REPLACE FUNCTION {GUARD_FUNCTION} RETURNS event_trigger "
                "LANGUAGE plpgsql AS $$ BEGIN END $$"
            ],
        )

        with pytest.raises(RuntimeError, match="has a body no Tracecat revision"):
            _alembic(
                monkeypatch, guard_db.migrator_url, "upgrade", OWNER_EXEMPTION_REVISION
            )

    def test_upgrade_warns_when_no_guard_fires(
        self,
        guard_db: GuardDb,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # b4074a0896b5 skipped the guard: the role couldn't create triggers.
        _alembic(monkeypatch, guard_db.db_url, "stamp", MIGRATION_REVISION)

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            _alembic(monkeypatch, guard_db.db_url, "upgrade", OWNER_EXEMPTION_REVISION)

        assert "isn't installed or doesn't fire" in caplog.text
        assert _guard_objects(guard_db.db_url) == []

    def test_single_owner_install_keeps_member_exemption(
        self, guard_db: GuardDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Off RDS one role owns the function and the triggers.
        _alembic(monkeypatch, guard_db.db_url, "upgrade", OWNER_EXEMPTION_REVISION)

        assert _guard_body(guard_db.db_url) == _revision_body(OWNER_EXEMPTION_REVISION)
        for case in BLOCKED_CASES:
            _run(guard_db.admin_url, _blocked(guard_db, case))
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))

    def test_manual_install_exempts_the_installing_role(
        self, guard_db: GuardDb
    ) -> None:
        _run_committed(guard_db.db_url, _install_statements(OWNER_EXEMPTION_REVISION))

        _run(guard_db.admin_url, _blocked(guard_db, "disable"))
        _assert_guard_rejects(guard_db.app_url, _blocked(guard_db, "disable"))
