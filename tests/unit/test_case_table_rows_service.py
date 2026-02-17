from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows.schemas import CaseTableRowLink
from tracecat.cases.rows.service import CaseTableRowService
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.db.models import CaseTableRow, Table
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def tables_service(session: AsyncSession, svc_admin_role: Role) -> TablesService:
    return TablesService(session=session, role=svc_admin_role)


async def _create_case(cases_service: CasesService):
    return await cases_service.create_case(
        CaseCreate(
            summary="Case for table row links",
            description="Case description",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )


async def _create_table_with_row(
    tables_service: TablesService,
) -> tuple[Table, uuid.UUID]:
    table = await tables_service.create_table(
        TableCreate(
            name=f"table_{uuid.uuid4().hex[:8]}",
            columns=[
                TableColumnCreate(name="value", type=SqlType.TEXT, nullable=True),
            ],
        )
    )
    row = await tables_service.insert_row(
        table,
        TableRowInsert(data={"value": "hello"}),
    )
    return table, uuid.UUID(str(row["id"]))


@pytest.mark.anyio
async def test_link_case_row_creates_link_and_event(
    session: AsyncSession,
    svc_role: Role,
    cases_service: CasesService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table, row_id = await _create_table_with_row(tables_service)

    service = CaseTableRowService(session=session, role=svc_role)
    link = await service.link_case_row(
        case,
        CaseTableRowLink(table_id=table.id, row_id=row_id),
    )

    assert link.case_id == case.id
    assert link.table_id == table.id
    assert link.row_id == row_id

    events = await cases_service.events.list_events(case)
    event = next(
        (evt for evt in events if evt.type == CaseEventType.TABLE_ROW_LINKED), None
    )
    assert event is not None
    assert event.data["table_id"] == str(table.id)
    assert event.data["row_id"] == str(row_id)
    assert event.data["table_name"] == table.name


@pytest.mark.anyio
async def test_link_case_row_is_idempotent(
    session: AsyncSession,
    svc_role: Role,
    cases_service: CasesService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table, row_id = await _create_table_with_row(tables_service)

    service = CaseTableRowService(session=session, role=svc_role)
    first = await service.link_case_row(
        case,
        CaseTableRowLink(table_id=table.id, row_id=row_id),
    )
    second = await service.link_case_row(
        case,
        CaseTableRowLink(table_id=table.id, row_id=row_id),
    )

    assert second.id == first.id
    count_stmt = (
        sa.select(sa.func.count())
        .select_from(CaseTableRow)
        .where(
            CaseTableRow.workspace_id == svc_role.workspace_id,
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == table.id,
            CaseTableRow.row_id == row_id,
        )
    )
    count = await session.scalar(count_stmt)
    assert count == 1


@pytest.mark.anyio
async def test_unlink_case_row_removes_link_and_emits_event(
    session: AsyncSession,
    svc_role: Role,
    cases_service: CasesService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table, row_id = await _create_table_with_row(tables_service)

    service = CaseTableRowService(session=session, role=svc_role)
    await service.link_case_row(
        case,
        CaseTableRowLink(table_id=table.id, row_id=row_id),
    )
    link = await service.get_link_by_table_row(case, table.id, row_id)
    assert link is not None

    await service.unlink_case_row(case, link)

    link_after_delete = await service.get_link_by_table_row(case, table.id, row_id)
    assert link_after_delete is None

    events = await cases_service.events.list_events(case)
    event = next(
        (evt for evt in events if evt.type == CaseEventType.TABLE_ROW_UNLINKED),
        None,
    )
    assert event is not None
    assert event.data["table_id"] == str(table.id)
    assert event.data["row_id"] == str(row_id)
    assert event.data["table_name"] == table.name


@pytest.mark.anyio
async def test_list_case_rows_for_cases_groups_rows(
    session: AsyncSession,
    svc_role: Role,
    cases_service: CasesService,
    tables_service: TablesService,
) -> None:
    case_one = await _create_case(cases_service)
    case_two = await _create_case(cases_service)
    table, row_id_one = await _create_table_with_row(tables_service)
    row_two = await tables_service.insert_row(
        table, TableRowInsert(data={"value": "world"})
    )
    row_id_two = uuid.UUID(str(row_two["id"]))

    service = CaseTableRowService(session=session, role=svc_role)
    await service.link_case_row(
        case_one,
        CaseTableRowLink(table_id=table.id, row_id=row_id_one),
    )
    await service.link_case_row(
        case_two,
        CaseTableRowLink(table_id=table.id, row_id=row_id_two),
    )

    rows_by_case = await service.list_case_rows_for_cases([case_one.id, case_two.id])
    assert len(rows_by_case[case_one.id]) == 1
    assert len(rows_by_case[case_two.id]) == 1
    assert rows_by_case[case_one.id][0].row_id == row_id_one
    assert rows_by_case[case_two.id][0].row_id == row_id_two
