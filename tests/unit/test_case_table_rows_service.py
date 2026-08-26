import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_ORG_ID
from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows import service as case_rows_service_module
from tracecat.cases.rows.schemas import (
    CaseTableRowBatchLinkResponse,
    CaseTableRowLinkCreate,
)
from tracecat.cases.rows.service import (
    MAX_TABLES_PER_CASE,
    CaseTableRowsService,
)
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.db.models import CaseEvent, CaseTableRow, Table, Workspace
from tracecat.exceptions import TracecatNotFoundError
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


async def _create_table_with_rows(
    tables_service: TablesService,
    *,
    name: str,
    values: Sequence[str],
) -> tuple[uuid.UUID, list[uuid.UUID]]:
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
    row_ids: list[uuid.UUID] = []
    for value in values:
        row = await tables_service.insert_row(
            table,
            TableRowInsert(data={"value": value}),
        )
        row_id = row.get("id")
        assert isinstance(row_id, uuid.UUID)
        row_ids.append(row_id)
    return table.id, row_ids


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


@pytest.mark.anyio
async def test_list_rows_reverse_pagination_cursors_round_trip(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    """Backward cursors return the adjacent page and keep both directions valid."""
    case = await _create_case(cases_service)
    table_id, _ = await _create_table_with_row(
        tables_service,
        name=f"case_rows_reverse_{uuid.uuid4().hex[:8]}",
        value="seed",
    )
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    links = [
        CaseTableRow(
            workspace_id=case_rows_service.workspace_id,
            case_id=case.id,
            table_id=table_id,
            row_id=uuid.uuid4(),
            created_at=base_time + timedelta(minutes=i),
            updated_at=base_time + timedelta(minutes=i),
        )
        for i in range(6)
    ]
    session.add_all(links)
    await session.commit()
    expected_ids = [
        link.id
        for link in sorted(links, key=lambda link: link.created_at, reverse=True)
    ]

    async def get_page(cursor: str | None = None, *, reverse: bool = False):
        return await case_rows_service.list_rows(
            case_id=case.id,
            limit=2,
            cursor=cursor,
            reverse=reverse,
            include_row_data=False,
        )

    page1 = await get_page()
    page2 = await get_page(page1.next_cursor)
    page3 = await get_page(page2.next_cursor)
    assert [item.id for item in page3.items] == expected_ids[4:6]
    assert page3.prev_cursor is not None

    back = await get_page(page3.prev_cursor, reverse=True)
    assert [item.id for item in back.items] == expected_ids[2:4]
    assert back.next_cursor is not None
    assert back.prev_cursor is not None

    forward_again = await get_page(back.next_cursor)
    assert [item.id for item in forward_again.items] == expected_ids[4:6]

    back_to_first = await get_page(back.prev_cursor, reverse=True)
    assert [item.id for item in back_to_first.items] == expected_ids[:2]
    assert back_to_first.next_cursor is not None
    assert back_to_first.prev_cursor is None


@pytest.mark.anyio
async def test_list_rows_filters_by_table_and_returns_exact_total(
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_a_id, table_a_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_filter_a_{uuid.uuid4().hex[:8]}",
        values=["a1", "a2", "a3"],
    )
    table_b_id, table_b_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_filter_b_{uuid.uuid4().hex[:8]}",
        values=["b1", "b2"],
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_a_id,
        row_ids=table_a_row_ids,
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_b_id,
        row_ids=table_b_row_ids,
    )

    first_page = await case_rows_service.list_rows(
        case_id=case.id,
        limit=2,
        include_row_data=False,
        table_id=table_a_id,
    )
    assert len(first_page.items) == 2
    assert all(item.table_id == table_a_id for item in first_page.items)
    assert first_page.total_estimate == 3
    assert first_page.next_cursor is not None

    second_page = await case_rows_service.list_rows(
        case_id=case.id,
        limit=2,
        cursor=first_page.next_cursor,
        include_row_data=False,
        table_id=table_a_id,
    )
    assert len(second_page.items) == 1
    assert second_page.items[0].table_id == table_a_id
    assert second_page.total_estimate == 3

    unfiltered = await case_rows_service.list_rows(
        case_id=case.id,
        limit=10,
        include_row_data=False,
    )
    assert len(unfiltered.items) == 5
    assert unfiltered.total_estimate is None


@pytest.mark.anyio
async def test_list_linked_tables_returns_counts_ordered_by_name(
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    zzz_table_id, zzz_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"zzz_case_rows_{uuid.uuid4().hex[:8]}",
        values=["z1", "z2", "z3"],
    )
    aaa_table_id, aaa_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"aaa_case_rows_{uuid.uuid4().hex[:8]}",
        values=["a1", "a2"],
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=zzz_table_id,
        row_ids=zzz_row_ids,
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=aaa_table_id,
        row_ids=aaa_row_ids,
    )

    linked_tables = await case_rows_service.list_linked_tables(case_id=case.id)

    assert [(linked.table_id, linked.row_count) for linked in linked_tables] == [
        (aaa_table_id, 2),
        (zzz_table_id, 3),
    ]

    fresh_case = await _create_case(cases_service)
    assert await case_rows_service.list_linked_tables(case_id=fresh_case.id) == []


@pytest.mark.anyio
async def test_link_rows_links_all_and_dedupes(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_dedupe_{uuid.uuid4().hex[:8]}",
        values=["one", "two", "three"],
    )

    result = await case_rows_service.link_rows(
        case=case,
        table_id=table_id,
        row_ids=[row_ids[0], row_ids[1], row_ids[1], row_ids[2]],
    )

    assert result.linked_count == 3
    assert result.already_linked_count == 0
    count = await session.scalar(
        select(func.count())
        .select_from(CaseTableRow)
        .where(CaseTableRow.case_id == case.id)
    )
    assert count == 3


@pytest.mark.anyio
async def test_link_rows_reports_already_linked(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_existing_{uuid.uuid4().hex[:8]}",
        values=["one", "two"],
    )
    await case_rows_service.link_row(
        case=case,
        params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_ids[0]),
    )

    result = await case_rows_service.link_rows(
        case=case,
        table_id=table_id,
        row_ids=row_ids,
    )

    assert result.linked_count == 1
    assert result.already_linked_count == 1
    count = await session.scalar(
        select(func.count())
        .select_from(CaseTableRow)
        .where(CaseTableRow.case_id == case.id)
    )
    assert count == 2


@pytest.mark.anyio
async def test_link_rows_raises_not_found_for_missing_row(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_missing_row_{uuid.uuid4().hex[:8]}",
        values=["one"],
    )

    with pytest.raises(TracecatNotFoundError, match="1 of 2 rows not found"):
        await case_rows_service.link_rows(
            case=case,
            table_id=table_id,
            row_ids=[row_ids[0], uuid.uuid4()],
        )

    count = await session.scalar(
        select(func.count())
        .select_from(CaseTableRow)
        .where(CaseTableRow.case_id == case.id)
    )
    assert count == 0


@pytest.mark.anyio
async def test_link_rows_raises_not_found_for_missing_table(
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
) -> None:
    case = await _create_case(cases_service)

    with pytest.raises(TracecatNotFoundError, match="Table not found"):
        await case_rows_service.link_rows(
            case=case,
            table_id=uuid.uuid4(),
            row_ids=[uuid.uuid4()],
        )


@pytest.mark.anyio
async def test_link_rows_enforces_row_limit(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    monkeypatch.setattr(case_rows_service_module, "MAX_LINKED_ROWS_PER_CASE", 2)
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_row_limit_{uuid.uuid4().hex[:8]}",
        values=["one", "two", "three"],
    )

    with pytest.raises(ValueError, match="at most 2 linked rows"):
        await case_rows_service.link_rows(
            case=case,
            table_id=table_id,
            row_ids=row_ids,
        )

    count = await session.scalar(
        select(func.count())
        .select_from(CaseTableRow)
        .where(CaseTableRow.case_id == case.id)
    )
    assert count == 0

    result = await case_rows_service.link_rows(
        case=case,
        table_id=table_id,
        row_ids=row_ids[:2],
    )
    assert result.linked_count == 2


@pytest.mark.anyio
async def test_link_rows_table_limit_blocks_new_table_but_allows_existing(
    monkeypatch: pytest.MonkeyPatch,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    monkeypatch.setattr(case_rows_service_module, "MAX_TABLES_PER_CASE", 2)
    case = await _create_case(cases_service)
    table_a_id, table_a_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_limit_a_{uuid.uuid4().hex[:8]}",
        values=["one", "extra"],
    )
    table_b_id, table_b_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_limit_b_{uuid.uuid4().hex[:8]}",
        values=["two"],
    )
    table_c_id, table_c_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_limit_c_{uuid.uuid4().hex[:8]}",
        values=["three"],
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_a_id,
        row_ids=[table_a_row_ids[0]],
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_b_id,
        row_ids=table_b_row_ids,
    )

    with pytest.raises(ValueError, match="at most 2 tables"):
        await case_rows_service.link_rows(
            case=case,
            table_id=table_c_id,
            row_ids=table_c_row_ids,
        )

    result = await case_rows_service.link_rows(
        case=case,
        table_id=table_a_id,
        row_ids=[table_a_row_ids[1]],
    )
    assert result.linked_count == 1


@pytest.mark.anyio
async def test_link_rows_emits_one_event_per_new_link(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_events_{uuid.uuid4().hex[:8]}",
        values=["one", "two", "three"],
    )

    result = await case_rows_service.link_rows(
        case=case,
        table_id=table_id,
        row_ids=row_ids,
    )
    event_count = await session.scalar(
        select(func.count())
        .select_from(CaseEvent)
        .where(
            CaseEvent.case_id == case.id,
            CaseEvent.type == CaseEventType.TABLE_ROW_LINKED,
        )
    )
    assert result.linked_count == 3
    assert event_count == 3

    duplicate_result = await case_rows_service.link_rows(
        case=case,
        table_id=table_id,
        row_ids=row_ids,
    )
    event_count = await session.scalar(
        select(func.count())
        .select_from(CaseEvent)
        .where(
            CaseEvent.case_id == case.id,
            CaseEvent.type == CaseEventType.TABLE_ROW_LINKED,
        )
    )
    assert duplicate_result.linked_count == 0
    assert event_count == 3


@pytest.mark.anyio
async def test_unlink_rows_deletes_matching_and_skips_missing(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_batch_unlink_{uuid.uuid4().hex[:8]}",
        values=["one", "two"],
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_id,
        row_ids=row_ids,
    )

    unlinked_count = await case_rows_service.unlink_rows(
        case=case,
        table_id=table_id,
        row_ids=[row_ids[0], uuid.uuid4()],
    )

    remaining = (
        (
            await session.execute(
                select(CaseTableRow.row_id).where(CaseTableRow.case_id == case.id)
            )
        )
        .scalars()
        .all()
    )
    event_count = await session.scalar(
        select(func.count())
        .select_from(CaseEvent)
        .where(
            CaseEvent.case_id == case.id,
            CaseEvent.type == CaseEventType.TABLE_ROW_UNLINKED,
        )
    )
    assert unlinked_count == 1
    assert remaining == [row_ids[1]]
    assert event_count == 1


@pytest.mark.anyio
async def test_unlink_rows_ignores_other_tables(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_a_id, table_a_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_unlink_other_a_{uuid.uuid4().hex[:8]}",
        values=["one"],
    )
    table_b_id, table_b_row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_unlink_other_b_{uuid.uuid4().hex[:8]}",
        values=["two"],
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_a_id,
        row_ids=table_a_row_ids,
    )
    await case_rows_service.link_rows(
        case=case,
        table_id=table_b_id,
        row_ids=table_b_row_ids,
    )

    unlinked_count = await case_rows_service.unlink_rows(
        case=case,
        table_id=table_a_id,
        row_ids=[table_a_row_ids[0], table_b_row_ids[0]],
    )

    remaining = (
        (
            await session.execute(
                select(CaseTableRow.table_id, CaseTableRow.row_id).where(
                    CaseTableRow.case_id == case.id
                )
            )
        )
        .tuples()
        .all()
    )
    assert unlinked_count == 1
    assert remaining == [(table_b_id, table_b_row_ids[0])]


@pytest.mark.anyio
async def test_unlink_rows_returns_zero_when_nothing_linked(
    session: AsyncSession,
    cases_service: CasesService,
    case_rows_service: CaseTableRowsService,
    tables_service: TablesService,
) -> None:
    case = await _create_case(cases_service)
    table_id, row_ids = await _create_table_with_rows(
        tables_service,
        name=f"case_rows_unlink_empty_{uuid.uuid4().hex[:8]}",
        values=["one"],
    )

    unlinked_count = await case_rows_service.unlink_rows(
        case=case,
        table_id=table_id,
        row_ids=row_ids,
    )

    event_count = await session.scalar(
        select(func.count())
        .select_from(CaseEvent)
        .where(
            CaseEvent.case_id == case.id,
            CaseEvent.type == CaseEventType.TABLE_ROW_UNLINKED,
        )
    )
    assert unlinked_count == 0
    assert event_count == 0


@pytest.mark.anyio
async def test_concurrent_link_rows_cannot_exceed_row_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard against two concurrent link requests both passing the row cap.

    The cap is enforced by reading an aggregate link count and then inserting,
    so without a row lock on the case both requests read the same pre-insert
    count, both believe there is room, and the case ends up over
    ``MAX_LINKED_ROWS_PER_CASE``. Each request runs on its own connection so the
    race is real rather than simulated.
    """
    link_limit = 3
    monkeypatch.setattr(
        case_rows_service_module, "MAX_LINKED_ROWS_PER_CASE", link_limit
    )

    # Own engine, not the ``session`` fixture: that fixture pins the test to a
    # single connection inside an outer transaction that is never committed, so
    # a second connection would neither see the seeded links nor be able to
    # acquire the case lock.
    engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        poolclass=NullPool,
        # Fail fast instead of hanging the suite if the lock is never released.
        connect_args={"server_settings": {"lock_timeout": "30s"}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    workspace_id = uuid.uuid4()
    role = Role(
        type="user",
        workspace_id=workspace_id,
        organization_id=TEST_ORG_ID,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )

    try:
        async with session_factory() as setup_session:
            setup_session.add(
                Workspace(
                    id=workspace_id,
                    name=f"case-rows-race-{workspace_id.hex[:8]}",
                    organization_id=TEST_ORG_ID,
                )
            )
            await setup_session.commit()

            tables_service = TablesService(session=setup_session, role=role)
            table_id, row_ids = await _create_table_with_rows(
                tables_service,
                name=f"case_rows_race_limit_{uuid.uuid4().hex[:8]}",
                values=["one", "two", "three", "four"],
            )
            case = await _create_case(CasesService(session=setup_session, role=role))
            case_id = case.id

            seeded = await CaseTableRowsService(
                session=setup_session, role=role
            ).link_rows(case=case, table_id=table_id, row_ids=row_ids[:2])
            assert seeded.linked_count == 2

        async def link_one(row_id: uuid.UUID) -> CaseTableRowBatchLinkResponse:
            async with session_factory() as task_session:
                service = CaseTableRowsService(session=task_session, role=role)
                task_case = await service.get_case_or_raise(case_id)
                return await service.link_rows(
                    case=task_case,
                    table_id=table_id,
                    row_ids=[row_id],
                )

        results = await asyncio.gather(
            link_one(row_ids[2]),
            link_one(row_ids[3]),
            return_exceptions=True,
        )

        linked = [
            result
            for result in results
            if isinstance(result, CaseTableRowBatchLinkResponse)
        ]
        errors = [result for result in results if isinstance(result, BaseException)]
        assert len(linked) == 1, results
        assert linked[0].linked_count == 1
        assert len(errors) == 1, results
        assert isinstance(errors[0], ValueError)
        assert f"at most {link_limit} linked rows" in str(errors[0])

        async with session_factory() as verify_session:
            total_links = await verify_session.scalar(
                select(func.count())
                .select_from(CaseTableRow)
                .where(CaseTableRow.case_id == case_id)
            )
        assert total_links == link_limit
    finally:
        async with session_factory() as cleanup_session:
            tables = (
                (
                    await cleanup_session.execute(
                        select(Table).where(Table.workspace_id == workspace_id)
                    )
                )
                .scalars()
                .all()
            )
            cleanup_tables_service = TablesService(session=cleanup_session, role=role)
            for table in tables:
                await cleanup_tables_service.delete_table(table)
            await cleanup_session.execute(
                sa.delete(Workspace).where(Workspace.id == workspace_id)
            )
            await cleanup_session.commit()
        await engine.dispose()
