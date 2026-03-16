from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate, CaseFieldCreate
from tracecat.cases.service import CaseFieldsService, CasesService
from tracecat.db.models import Workspace
from tracecat.db.rls import set_rls_context
from tracecat.exceptions import TracecatNotFoundError
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableCreate,
    TableRowInsert,
)
from tracecat.tables.service import TableEditorService, TablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@pytest.fixture(scope="session")
def reader_role_name() -> Iterator[str]:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master").replace("-", "_")
    role_name = f"rls_reader_{worker_id}_{uuid.uuid4().hex[:8]}"
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE ROLE "{role_name}"'))
        yield role_name
    finally:
        with engine.connect() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{role_name}"'))
        engine.dispose()


@pytest.fixture
async def svc_workspace_b(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[Workspace, None]:
    workspace = Workspace(
        name="test-workspace-b",
        organization_id=svc_workspace.organization_id,
    )
    session.add(workspace)
    await session.commit()
    yield workspace


@pytest.fixture
async def svc_role_b(svc_role: Role, svc_workspace_b: Workspace) -> Role:
    return svc_role.model_copy(
        update={"workspace_id": svc_workspace_b.id, "user_id": uuid.uuid4()}
    )


@pytest.fixture
async def tables_service(session: AsyncSession, svc_role: Role) -> TablesService:
    return TablesService(session=session, role=svc_role)


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def case_fields_service(
    session: AsyncSession, svc_role: Role
) -> CaseFieldsService:
    return CaseFieldsService(session=session, role=svc_role)


async def _grant_reader_access(
    session: AsyncSession, *, role_name: str, schema_name: str, table_name: str
) -> None:
    await session.execute(
        text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role_name}"')
    )
    await session.execute(
        text(f'GRANT SELECT ON TABLE "{schema_name}"."{table_name}" TO "{role_name}"')
    )
    await session.flush()


async def _assert_workspace_visible_rows(
    session: AsyncSession,
    *,
    db_role_name: str,
    role: Role,
    schema_name: str,
    table_name: str,
    expected_row_id: uuid.UUID | None,
) -> None:
    editor = TableEditorService(
        session=session,
        role=role,
        schema_name=schema_name,
        table_name=table_name,
    )
    await session.execute(text(f'SET ROLE "{db_role_name}"'))
    try:
        await set_rls_context(
            session,
            org_id=role.organization_id,
            workspace_id=role.workspace_id,
            user_id=role.user_id,
            bypass=False,
        )
        page = await editor.list_rows(limit=100, cursor=None, reverse=False)
        row_ids = [row["id"] for row in page.items]
        if expected_row_id is None:
            assert row_ids == []
        else:
            assert row_ids == [expected_row_id]
            row = await editor.get_row(expected_row_id)
            assert row["id"] == expected_row_id
    finally:
        await session.execute(text("RESET ROLE"))


@pytest.mark.anyio
class TestDynamicWorkspaceServiceRls:
    async def test_tables_editor_blocks_cross_workspace_reads(
        self,
        session: AsyncSession,
        tables_service: TablesService,
        svc_role: Role,
        svc_role_b: Role,
        reader_role_name: str,
    ) -> None:
        table = await tables_service.create_table(
            TableCreate(
                name="workspace_alerts",
                columns=[
                    TableColumnCreate(
                        name="name", type=SqlType.TEXT, nullable=True, default=None
                    )
                ],
            )
        )
        inserted = await tables_service.insert_row(
            table, TableRowInsert(data={"name": "workspace-a-visible"})
        )

        schema_name = tables_service._get_schema_name()
        await _grant_reader_access(
            session,
            role_name=reader_role_name,
            schema_name=schema_name,
            table_name=table.name,
        )

        await _assert_workspace_visible_rows(
            session,
            db_role_name=reader_role_name,
            role=svc_role,
            schema_name=schema_name,
            table_name=table.name,
            expected_row_id=inserted["id"],
        )
        await _assert_workspace_visible_rows(
            session,
            db_role_name=reader_role_name,
            role=svc_role_b,
            schema_name=schema_name,
            table_name=table.name,
            expected_row_id=None,
        )

    async def test_case_fields_editor_blocks_cross_workspace_reads(
        self,
        session: AsyncSession,
        cases_service: CasesService,
        case_fields_service: CaseFieldsService,
        svc_role: Role,
        svc_role_b: Role,
        reader_role_name: str,
    ) -> None:
        await case_fields_service.create_field(
            CaseFieldCreate(name="triage_note", type=SqlType.TEXT)
        )
        case = await cases_service.create_case(
            CaseCreate(
                summary="Workspace A case",
                description="cross-workspace rls test",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        row = await case_fields_service.upsert_field_values(
            case, {"triage_note": "workspace-a-visible"}
        )

        await _grant_reader_access(
            session,
            role_name=reader_role_name,
            schema_name=case_fields_service.schema_name,
            table_name=case_fields_service.sanitized_table_name,
        )

        await _assert_workspace_visible_rows(
            session,
            db_role_name=reader_role_name,
            role=svc_role,
            schema_name=case_fields_service.schema_name,
            table_name=case_fields_service.sanitized_table_name,
            expected_row_id=row["id"],
        )

        editor = TableEditorService(
            session=session,
            role=svc_role_b,
            schema_name=case_fields_service.schema_name,
            table_name=case_fields_service.sanitized_table_name,
        )
        await session.execute(text(f'SET ROLE "{reader_role_name}"'))
        try:
            await set_rls_context(
                session,
                org_id=svc_role_b.organization_id,
                workspace_id=svc_role_b.workspace_id,
                user_id=svc_role_b.user_id,
                bypass=False,
            )
            page = await editor.list_rows(limit=100, cursor=None, reverse=False)
            assert page.items == []
            with pytest.raises(TracecatNotFoundError):
                await editor.get_row(row["id"])
        finally:
            await session.execute(text("RESET ROLE"))
