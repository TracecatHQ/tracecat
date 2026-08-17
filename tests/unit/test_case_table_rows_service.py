import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows import service as case_rows_service_module
from tracecat.cases.rows.schemas import CaseTableRowLinkCreate
from tracecat.cases.rows.service import (
    MAX_TABLES_PER_CASE,
    CaseTableRowsService,
)
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.db.models import CaseTableRow, Table
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
async def test_link_row_returns_existing_link_for_duplicate(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_id = await _create_table_with_row(
        tables_service,
        name=f"case_rows_duplicate_{uuid.uuid4().hex[:8]}",
        value="seed",
    )

    first_link = await case_rows_service.link_row(
        case=case,
        params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
    )
    second_link = await case_rows_service.link_row(
        case=case,
        params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
    )

    count = await session.scalar(
        select(func.count())
        .select_from(CaseTableRow)
        .where(
            CaseTableRow.workspace_id == case_rows_service.workspace_id,
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == table_id,
            CaseTableRow.row_id == row_id,
        )
    )
    assert second_link.id == first_link.id
    assert count == 1


@pytest.mark.anyio
async def test_link_row_returns_existing_link_when_limit_reached_after_initial_miss(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    test_link_limit = 2
    monkeypatch.setattr(
        case_rows_service_module, "MAX_LINKED_ROWS_PER_CASE", test_link_limit
    )

    case = await _create_case(cases_service)
    table_id, row_id = await _create_table_with_row(
        tables_service,
        name=f"case_rows_race_duplicate_{uuid.uuid4().hex[:8]}",
        value="seed",
    )

    existing_link = CaseTableRow(
        workspace_id=case_rows_service.workspace_id,
        case_id=case.id,
        table_id=table_id,
        row_id=row_id,
    )
    filler_links = [
        CaseTableRow(
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=uuid.uuid4(),
        )
        for _ in range(test_link_limit - 1)
    ]
    session.add_all([existing_link, *filler_links])
    await session.commit()
    await session.refresh(existing_link)

    original_get_existing_link = case_rows_service._get_existing_link
    calls = 0

    async def miss_initial_lookup(
        *, case_id: uuid.UUID, table_id: uuid.UUID, row_id: uuid.UUID
    ) -> CaseTableRow | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return await original_get_existing_link(
            case_id=case_id,
            table_id=table_id,
            row_id=row_id,
        )

    monkeypatch.setattr(case_rows_service, "_get_existing_link", miss_initial_lookup)

    link = await case_rows_service.link_row(
        case=case,
        params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
    )

    assert link.id == existing_link.id
    assert calls == 2


@pytest.mark.anyio
async def test_list_rows_batches_hydration_by_table(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, first_row_id = await _create_table_with_row(
        tables_service,
        name=f"case_rows_batch_hydration_{uuid.uuid4().hex[:8]}",
        value="first",
    )
    table = await tables_service.get_table(table_id)
    second_row = await tables_service.insert_row(
        table,
        TableRowInsert(data={"value": "second"}),
    )
    second_row_id = second_row.get("id")
    assert isinstance(second_row_id, uuid.UUID)

    for row_id in (first_row_id, second_row_id):
        await case_rows_service.link_row(
            case=case,
            params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
        )

    missing_row_id = uuid.uuid4()
    session.add(
        CaseTableRow(
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=missing_row_id,
        )
    )
    await session.commit()

    original_get_rows = case_rows_service.tables.get_rows
    get_rows_calls: list[tuple[uuid.UUID, set[uuid.UUID]]] = []

    async def tracked_get_rows(
        table: Table, row_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        get_rows_calls.append((table.id, set(row_ids)))
        return await original_get_rows(table, row_ids)

    monkeypatch.setattr(case_rows_service.tables, "get_rows", tracked_get_rows)

    page = await case_rows_service.list_rows(
        case_id=case.id,
        limit=10,
        include_row_data=True,
    )

    assert get_rows_calls == [(table_id, {first_row_id, second_row_id, missing_row_id})]
    rows_by_id = {row.row_id: row for row in page.items}
    first_row = rows_by_id[first_row_id]
    assert first_row.row_data is not None
    assert first_row.row_data["value"] == "first"
    assert first_row.is_row_available is True
    second_row = rows_by_id[second_row_id]
    assert second_row.row_data is not None
    assert second_row.row_data["value"] == "second"
    assert second_row.is_row_available is True
    missing_row = rows_by_id[missing_row_id]
    assert missing_row.row_data is None
    assert missing_row.is_row_available is False


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
