"""Ownership of dynamic schemas and tables under TRACECAT__DB_DYNAMIC_OWNER_ROLE.

Invariants:
- Set: a non-superuser login role that is a member of the owner role creates
  ``tables_*`` / ``custom_fields_*`` schemas and tables, and the owner role,
  not the login role, owns them. Later writes in the same transaction still
  work, because the login role is never switched to the owner role.
- Forced RLS still applies to a role that inherits ownership: a member of
  the owner role without BYPASSRLS sees only its own workspace's rows.
- Unset: the creating role keeps ownership.
- With the dynamic RLS guard (b4074a0896b5) installed and the login role as
  ``session_user``, the app's own DDL on dynamic tables still works: creating,
  altering and dropping tables, columns and case fields, and re-initializing
  an existing case fields table. Disabling RLS as that role fails.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from asyncpg.exceptions import InsufficientPrivilegeError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.schemas import CaseFieldCreate
from tracecat.cases.service import CaseFieldsService
from tracecat.db.rls import set_rls_context
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
)
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.usefixtures("db")

GUARD_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b4074a0896b5_guard_rls_on_dynamic_workspace_tables.py"
)


def _guard_statements() -> tuple[str, ...]:
    """Load the guard SQL from its migration, so tests can't drift from it."""
    spec = importlib.util.spec_from_file_location("dynamic_rls_guard", GUARD_MIGRATION)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {GUARD_MIGRATION}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GUARD_STATEMENTS


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@dataclass(frozen=True, slots=True)
class OwnerRoles:
    owner: str
    login: str
    reader: str


@pytest.fixture(scope="session")
def owner_roles() -> Iterator[OwnerRoles]:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master").replace("-", "_")
    suffix = f"{worker_id}_{uuid.uuid4().hex[:8]}"
    # The % in the owner name checks it never reaches a %-format string.
    roles = OwnerRoles(
        owner=f"dyn%owner_{suffix}",
        login=f"dyn_app_{suffix}",
        reader=f"dyn_reader_{suffix}",
    )
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE ROLE "{roles.owner}" NOLOGIN'))
            # BYPASSRLS keeps the test about ownership, not public-table RLS.
            conn.execute(text(f'CREATE ROLE "{roles.login}" LOGIN BYPASSRLS'))
            conn.execute(text(f'GRANT "{roles.owner}" TO "{roles.login}"'))
            conn.execute(text(f'CREATE ROLE "{roles.reader}" LOGIN'))
            conn.execute(text(f'GRANT "{roles.owner}" TO "{roles.reader}"'))
        yield roles
    finally:
        with engine.connect() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{roles.reader}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{roles.login}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{roles.owner}"'))
        engine.dispose()


@pytest.fixture
async def as_login_role(
    session: AsyncSession, owner_roles: OwnerRoles, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Run the test as the non-superuser login role with the setting on.

    Grants live in the test transaction and roll back with it.
    """
    await _grant_login_role(session, owner_roles, monkeypatch)
    await session.execute(text(f'SET ROLE "{owner_roles.login}"'))
    try:
        yield
    finally:
        await session.execute(text("RESET ROLE"))


@pytest.fixture
async def as_guarded_login_role(
    session: AsyncSession, owner_roles: OwnerRoles, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Run as the login role with the dynamic RLS guard installed.

    The guard checks ``session_user``, which ``SET ROLE`` leaves unchanged,
    so this switches the session authorization instead. The guard and the
    grants live in the test transaction and roll back with it.
    """
    await _grant_login_role(session, owner_roles, monkeypatch)
    for statement in _guard_statements():
        await session.execute(text(statement))
    await session.execute(text(f'SET SESSION AUTHORIZATION "{owner_roles.login}"'))
    try:
        yield
    finally:
        await session.execute(text("RESET SESSION AUTHORIZATION"))


async def _grant_login_role(
    session: AsyncSession, owner_roles: OwnerRoles, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, login = owner_roles.owner, owner_roles.login
    db_name = TEST_DB_CONFIG.test_db_name
    await session.execute(
        text(f'GRANT CREATE ON DATABASE "{db_name}" TO "{owner}", "{login}"')
    )
    await session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{login}"'))
    await session.execute(
        text(f'GRANT ALL ON ALL TABLES IN SCHEMA public TO "{login}"')
    )
    await session.execute(
        text(f'GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO "{login}"')
    )
    monkeypatch.setattr(config, "TRACECAT__DB_DYNAMIC_OWNER_ROLE", owner)


async def _schema_owner(session: AsyncSession, schema_name: str) -> str:
    result = await session.execute(
        text(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
            "WHERE nspname = :schema_name"
        ),
        {"schema_name": schema_name},
    )
    return result.scalar_one()


async def _table_owner(session: AsyncSession, schema_name: str, table: str) -> str:
    result = await session.execute(
        text(
            "SELECT tableowner FROM pg_tables "
            "WHERE schemaname = :schema_name AND tablename = :table"
        ),
        {"schema_name": schema_name, "table": table},
    )
    return result.scalar_one()


async def _visible_rows(
    session: AsyncSession,
    role: Role,
    workspace_id: uuid.UUID | None,
    schema_name: str,
    table: str,
) -> int:
    await set_rls_context(
        session,
        org_id=role.organization_id,
        workspace_id=workspace_id,
        user_id=role.user_id,
        bypass=False,
    )
    result = await session.execute(
        text(f'SELECT count(*) FROM "{schema_name}"."{table}"')
    )
    return result.scalar_one()


async def _create_lookup_table(
    session: AsyncSession, role: Role, name: str = "owned_alerts"
) -> tuple[str, str]:
    service = TablesService(session=session, role=role)
    table = await service.create_table(
        TableCreate(
            name=name,
            columns=[
                TableColumnCreate(
                    name="name", type=SqlType.TEXT, nullable=True, default=None
                )
            ],
        )
    )
    # A write after the DDL, in the same transaction.
    await service.insert_row(table, TableRowInsert(data={"name": "a"}))
    return service._get_schema_name(), table.name


async def _create_case_field(session: AsyncSession, role: Role) -> tuple[str, str]:
    service = CaseFieldsService(session=session, role=role)
    await service.create_field(CaseFieldCreate(name="triage_note", type=SqlType.TEXT))
    return service.schema_name, service.sanitized_table_name


@pytest.mark.anyio
class TestDynamicOwnerRole:
    @pytest.mark.usefixtures("as_login_role")
    async def test_lookup_table_owned_by_owner_role(
        self, session: AsyncSession, svc_role: Role, owner_roles: OwnerRoles
    ) -> None:
        schema_name, table_name = await _create_lookup_table(session, svc_role)
        # Second table: the schema already exists and is already owned.
        _, second_table = await _create_lookup_table(
            session, svc_role, name="owned_alerts_2"
        )

        assert await _schema_owner(session, schema_name) == owner_roles.owner
        assert await _table_owner(session, schema_name, table_name) == owner_roles.owner
        assert (
            await _table_owner(session, schema_name, second_table) == owner_roles.owner
        )

        # Inherited owner without BYPASSRLS: forced RLS still filters by workspace.
        await session.execute(text(f'SET ROLE "{owner_roles.reader}"'))
        own_rows = await _visible_rows(
            session, svc_role, svc_role.workspace_id, schema_name, table_name
        )
        other_rows = await _visible_rows(
            session, svc_role, uuid.uuid4(), schema_name, table_name
        )
        assert (own_rows, other_rows) == (1, 0)

    @pytest.mark.usefixtures("as_login_role")
    async def test_case_fields_owned_by_owner_role(
        self, session: AsyncSession, svc_role: Role, owner_roles: OwnerRoles
    ) -> None:
        schema_name, table_name = await _create_case_field(session, svc_role)

        assert await _schema_owner(session, schema_name) == owner_roles.owner
        assert await _table_owner(session, schema_name, table_name) == owner_roles.owner

    @pytest.mark.usefixtures("as_login_role")
    async def test_owner_role_the_login_is_not_a_member_of_keeps_creator(
        self,
        session: AsyncSession,
        svc_role: Role,
        owner_roles: OwnerRoles,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The role exists but the login can't act as it: creation still works."""
        monkeypatch.setattr(
            config, "TRACECAT__DB_DYNAMIC_OWNER_ROLE", owner_roles.reader
        )

        schema_name, table_name = await _create_lookup_table(session, svc_role)

        assert await _schema_owner(session, schema_name) == owner_roles.login
        assert await _table_owner(session, schema_name, table_name) == owner_roles.login

    @pytest.mark.usefixtures("as_login_role")
    @pytest.mark.parametrize(
        ("option", "database_create"),
        [("SET FALSE", True), ("INHERIT FALSE", True), ("SET TRUE", False)],
    )
    async def test_unusable_owner_role_keeps_creator(
        self,
        session: AsyncSession,
        svc_role: Role,
        owner_roles: OwnerRoles,
        monkeypatch: pytest.MonkeyPatch,
        option: str,
        database_create: bool,
    ) -> None:
        """PG 16+: the handover needs SET and the owner's database CREATE (ALTER
        SCHEMA ... OWNER TO), and INHERIT (later DDL in the schema)."""
        version = (await session.execute(text("SHOW server_version_num"))).scalar_one()
        if int(version) < 160000:
            pytest.skip("the SET grant option needs PostgreSQL 16")
        no_set = f"dyn_noset_{uuid.uuid4().hex[:8]}"
        # Created in the test transaction, so it rolls back with it.
        await session.execute(text("RESET ROLE"))
        await session.execute(text(f'CREATE ROLE "{no_set}" NOLOGIN'))
        await session.execute(
            text(f'GRANT "{no_set}" TO "{owner_roles.login}" WITH {option}')
        )
        if database_create:
            db_name = TEST_DB_CONFIG.test_db_name
            await session.execute(
                text(f'GRANT CREATE ON DATABASE "{db_name}" TO "{no_set}"')
            )
        await session.execute(text(f'SET ROLE "{owner_roles.login}"'))
        monkeypatch.setattr(config, "TRACECAT__DB_DYNAMIC_OWNER_ROLE", no_set)

        schema_name, table_name = await _create_lookup_table(session, svc_role)

        assert await _schema_owner(session, schema_name) == owner_roles.login
        assert await _table_owner(session, schema_name, table_name) == owner_roles.login

    async def test_unset_keeps_creator_as_owner(
        self,
        session: AsyncSession,
        svc_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(config, "TRACECAT__DB_DYNAMIC_OWNER_ROLE", None)
        creator = (await session.execute(text("SELECT current_user"))).scalar_one()

        tables_schema, lookup_table = await _create_lookup_table(session, svc_role)
        fields_schema, fields_table = await _create_case_field(session, svc_role)

        assert await _schema_owner(session, tables_schema) == creator
        assert await _table_owner(session, tables_schema, lookup_table) == creator
        assert await _schema_owner(session, fields_schema) == creator
        assert await _table_owner(session, fields_schema, fields_table) == creator

    async def test_missing_owner_role_keeps_creator_as_owner(
        self,
        session: AsyncSession,
        svc_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Configured before the role exists (role setup hasn't run): creation still works."""
        missing = f"not_created_yet_{uuid.uuid4().hex[:8]}"
        monkeypatch.setattr(config, "TRACECAT__DB_DYNAMIC_OWNER_ROLE", missing)
        creator = (await session.execute(text("SELECT current_user"))).scalar_one()

        tables_schema, lookup_table = await _create_lookup_table(session, svc_role)

        assert await _schema_owner(session, tables_schema) == creator
        assert await _table_owner(session, tables_schema, lookup_table) == creator


async def _session_user(session: AsyncSession) -> str:
    return (await session.execute(text("SELECT session_user"))).scalar_one()


@pytest.mark.anyio
class TestDynamicRlsGuard:
    @pytest.mark.usefixtures("as_guarded_login_role")
    async def test_lookup_table_ddl_passes_guard(
        self, session: AsyncSession, svc_role: Role, owner_roles: OwnerRoles
    ) -> None:
        assert await _session_user(session) == owner_roles.login
        service = TablesService(session=session, role=svc_role)
        table = await service.create_table(
            TableCreate(
                name="guarded_alerts",
                columns=[TableColumnCreate(name="name", type=SqlType.TEXT)],
            )
        )
        column = await service.create_column(
            table, TableColumnCreate(name="severity", type=SqlType.TEXT)
        )
        column = await service.update_column(
            column, TableColumnUpdate(name="level", nullable=False, default="low")
        )
        await service.insert_row(table, TableRowInsert(data={"name": "a"}))
        await service.delete_column(column)
        await service.delete_table(table)

    @pytest.mark.usefixtures("as_guarded_login_role")
    async def test_case_field_ddl_passes_guard(
        self, session: AsyncSession, svc_role: Role, owner_roles: OwnerRoles
    ) -> None:
        assert await _session_user(session) == owner_roles.login
        service = CaseFieldsService(session=session, role=svc_role)
        await service.create_field(
            CaseFieldCreate(name="triage_note", type=SqlType.TEXT)
        )
        # Re-initializing an existing table keeps its policy instead of
        # dropping it, which the guard would reject.
        await service.initialize_workspace_schema()
        await service.delete_field("triage_note")
        await service.drop_workspace_schema()

    @pytest.mark.usefixtures("as_guarded_login_role")
    async def test_guard_blocks_disabling_rls(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        schema_name, table_name = await _create_lookup_table(session, svc_role)

        with pytest.raises(DBAPIError) as exc_info:
            async with session.begin_nested():
                await session.execute(
                    text(
                        f'ALTER TABLE "{schema_name}"."{table_name}" '
                        "DISABLE ROW LEVEL SECURITY"
                    )
                )
        assert exc_info.value.orig is not None
        assert isinstance(exc_info.value.orig.__cause__, InsufficientPrivilegeError)
