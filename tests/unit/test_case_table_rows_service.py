import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows.schemas import CaseTableRowLinkCreate
from tracecat.cases.rows.service import (
    MAX_TABLES_PER_CASE,
    CaseTableRowsService,
)
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.db.models import CaseTableRow
from tracecat.pagination import BaseCursorPaginator
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def case_rows_service(
    session: AsyncSession, svc_role: Role
) -> CaseTableRowsService:
    return CaseTableRowsService(session=session, role=svc_role)


@pytest.fixture
async def tables_service(session: AsyncSession, svc_role: Role) -> TablesService:
    return TablesService(session=session, role=svc_role)


async def _create_case(cases_service: CasesService):
    return await cases_service.create_case(
        CaseCreate(
            summary="Case rows test",
            description="Case rows pagination and linking test",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )


async def _create_table_with_row(
    tables_service: TablesService,
    *,
    name: str,
    value: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    table = await tables_service.create_table(
        TableCreate(
            name=name,
            columns=[
                TableColumnCreate(
                    name="value",
                    type=SqlType.TEXT,
                    nullable=True,
                    default=None,
                )
            ],
        )
    )
    row = await tables_service.insert_row(
        table,
        TableRowInsert(data={"value": value}),
    )
    row_id = row.get("id")
    assert isinstance(row_id, uuid.UUID)
    return table.id, row_id


@pytest.mark.anyio
async def test_link_row_allows_existing_table_when_table_limit_reached(
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)

    first_table_id: uuid.UUID | None = None
    for idx in range(MAX_TABLES_PER_CASE):
        table_id, row_id = await _create_table_with_row(
            tables_service,
            name=f"case_rows_cap_{idx}_{uuid.uuid4().hex[:8]}",
            value=f"value-{idx}",
        )
        if first_table_id is None:
            first_table_id = table_id
        await case_rows_service.link_row(
            case=case,
            params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
        )

    assert first_table_id is not None
    first_table = await tables_service.get_table(first_table_id)
    extra_row = await tables_service.insert_row(
        first_table,
        TableRowInsert(data={"value": "extra"}),
    )
    extra_row_id = extra_row.get("id")
    assert isinstance(extra_row_id, uuid.UUID)

    link = await case_rows_service.link_row(
        case=case,
        params=CaseTableRowLinkCreate(table_id=first_table_id, row_id=extra_row_id),
    )

    assert link.case_id == case.id
    assert link.table_id == first_table_id
    assert link.row_id == extra_row_id


@pytest.mark.anyio
async def test_list_rows_cursor_uses_created_at_and_id_order(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, _ = await _create_table_with_row(
        tables_service,
        name=f"case_rows_cursor_{uuid.uuid4().hex[:8]}",
        value="seed",
    )

    newest_small_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    middle_large_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    older_small_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    oldest_small_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    links = [
        CaseTableRow(
            id=newest_small_id,
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=uuid.uuid4(),
            created_at=base_time + timedelta(minutes=3),
            updated_at=base_time + timedelta(minutes=3),
        ),
        CaseTableRow(
            id=middle_large_id,
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=uuid.uuid4(),
            created_at=base_time + timedelta(minutes=2),
            updated_at=base_time + timedelta(minutes=2),
        ),
        CaseTableRow(
            id=older_small_id,
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=uuid.uuid4(),
            created_at=base_time + timedelta(minutes=1),
            updated_at=base_time + timedelta(minutes=1),
        ),
        CaseTableRow(
            id=oldest_small_id,
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=uuid.uuid4(),
            created_at=base_time,
            updated_at=base_time,
        ),
    ]
    session.add_all(links)
    await session.commit()

    page1 = await case_rows_service.list_rows(
        case_id=case.id,
        limit=2,
        include_row_data=False,
    )
    assert [item.id for item in page1.items] == [newest_small_id, middle_large_id]
    assert page1.next_cursor is not None

    page2 = await case_rows_service.list_rows(
        case_id=case.id,
        limit=2,
        cursor=page1.next_cursor,
        include_row_data=False,
    )
    assert [item.id for item in page2.items] == [older_small_id, oldest_small_id]
    combined_ids = [item.id for item in page1.items] + [item.id for item in page2.items]
    assert combined_ids == [
        newest_small_id,
        middle_large_id,
        older_small_id,
        oldest_small_id,
    ]

    legacy_cursor = BaseCursorPaginator.encode_cursor(middle_large_id)
    legacy_page = await case_rows_service.list_rows(
        case_id=case.id,
        limit=2,
        cursor=legacy_cursor,
        include_row_data=False,
    )
    assert [item.id for item in legacy_page.items] == [older_small_id, oldest_small_id]
