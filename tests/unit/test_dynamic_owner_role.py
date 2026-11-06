"""Ownership of dynamic schemas and tables under TRACECAT__DB_DYNAMIC_OWNER_ROLE.

Invariants:
- Set: a non-superuser login role that is a member of the owner role creates
  ``tables_*`` / ``custom_fields_*`` schemas and tables, and the owner role,
  not the login role, owns them. Later writes in the same transaction still
  work, because the login role is never switched to the owner role.
- Unset: the creating role keeps ownership.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.schemas import CaseFieldCreate
from tracecat.cases.service import CaseFieldsService
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@dataclass(frozen=True, slots=True)
class OwnerRoles:
    owner: str
    login: str


@pytest.fixture(scope="session")
def owner_roles() -> Iterator[OwnerRoles]:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master").replace("-", "_")
    suffix = f"{worker_id}_{uuid.uuid4().hex[:8]}"
    roles = OwnerRoles(owner=f"dyn_owner_{suffix}", login=f"dyn_app_{suffix}")
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE ROLE "{roles.owner}" NOLOGIN'))
            # BYPASSRLS keeps the test about ownership, not public-table RLS.
            conn.execute(text(f'CREATE ROLE "{roles.login}" LOGIN BYPASSRLS'))
            conn.execute(text(f'GRANT "{roles.owner}" TO "{roles.login}"'))
        yield roles
    finally:
        with engine.connect() as conn:
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
    await session.execute(text(f'SET ROLE "{login}"'))
    try:
        yield
    finally:
        await session.execute(text("RESET ROLE"))


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

    @pytest.mark.usefixtures("as_login_role")
    async def test_case_fields_owned_by_owner_role(
        self, session: AsyncSession, svc_role: Role, owner_roles: OwnerRoles
    ) -> None:
        schema_name, table_name = await _create_case_field(session, svc_role)

        assert await _schema_owner(session, schema_name) == owner_roles.owner
        assert await _table_owner(session, schema_name, table_name) == owner_roles.owner

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
