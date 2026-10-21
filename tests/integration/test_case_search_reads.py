"""Live query/projection checks for bounded case-page hydration."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState
from sqlalchemy.schema import DropTable
from sqlalchemy.sql.schema import MetaData
from sqlalchemy.sql.schema import Table as SqlTable

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseFieldCreate
from tracecat.cases.search_schemas import CaseSearchRequest
from tracecat.cases.service import CasesService
from tracecat.db.models import (
    Case,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDropdownValue,
    CaseDuration,
    CaseDurationDefinition,
    CaseTableRow,
    CaseTag,
    User,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tiers import defaults as tier_defaults

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.usefixtures("db")]


@dataclass(frozen=True, slots=True)
class CapturedQuery:
    statement: str
    parameters: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class SyntheticSearchCases:
    ids: tuple[uuid.UUID, ...]
    surrogate_ids: tuple[int, ...]
    selected_option_id: uuid.UUID
    unused_option_id: uuid.UUID


@contextmanager
def capture_search_queries(session: AsyncSession) -> Iterator[list[CapturedQuery]]:
    """Capture SELECTs only from this isolated session's test engine."""
    queries: list[CapturedQuery] = []

    def record(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: tuple[object, ...],
        context: object,
        executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("SELECT"):
            queries.append(CapturedQuery(statement, parameters))

    engine = session.get_bind().engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield queries
    finally:
        event.remove(engine, "before_cursor_execute", record)


def population_counts(queries: list[CapturedQuery]) -> list[CapturedQuery]:
    """Exclude grouped per-page task/sub-case counts from population scans."""
    return [
        query
        for query in queries
        if query.statement.lstrip().startswith("SELECT count(")
    ]


@pytest.fixture
async def synthetic_search_cases(
    session: AsyncSession, svc_role: Role
) -> SyntheticSearchCases:
    assert svc_role.workspace_id is not None
    user = User(
        id=uuid.uuid4(),
        email="synthetic-assignee@example.com",
        hashed_password="synthetic",
        settings={},
    )
    tag = CaseTag(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        name="Synthetic tag",
        ref="synthetic_tag",
        color="blue",
    )
    dropdown = CaseDropdownDefinition(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        name="Synthetic classification",
        ref="synthetic_classification",
    )
    selected = CaseDropdownOption(
        id=uuid.uuid4(), definition=dropdown, label="Selected", ref="selected"
    )
    unused = CaseDropdownOption(
        id=uuid.uuid4(), definition=dropdown, label="Unused", ref="unused"
    )
    duration_definition = CaseDurationDefinition(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        name="Synthetic duration",
        start_event_type=CaseEventType.CASE_CREATED,
        end_event_type=CaseEventType.CASE_CLOSED,
    )
    cases: list[Case] = []
    for number in range(1, 6):
        case = Case(
            id=uuid.uuid4(),
            case_number=number,
            workspace_id=svc_role.workspace_id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=number),
            summary=f"Synthetic candidate {number}",
            description="Unused case description",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            status=CaseStatus.NEW if number <= 3 else CaseStatus.CLOSED,
            payload={"synthetic_blob": "payload-content " * 4096},
            assignee=user,
            tags=[tag],
        )
        case.dropdown_values = [
            CaseDropdownValue(id=uuid.uuid4(), definition=dropdown, option=selected)
        ]
        case.durations = [
            CaseDuration(
                id=uuid.uuid4(),
                workspace_id=svc_role.workspace_id,
                definition=duration_definition,
                duration=timedelta(seconds=number),
            )
        ]
        cases.append(case)
    cases[1].parent_id = cases[0].id
    session.add_all([*cases, unused])
    await session.flush()
    result = SyntheticSearchCases(
        tuple(case.id for case in cases),
        tuple(case.surrogate_id for case in cases),
        selected.id,
        unused.id,
    )
    session.expunge_all()
    return result


async def test_population_count_can_be_skipped_on_initial_and_continuation_pages(
    session: AsyncSession,
    svc_role: Role,
    synthetic_search_cases: SyntheticSearchCases,
) -> None:
    service = CasesService(session, svc_role)
    with capture_search_queries(session) as legacy_queries:
        legacy = await service.search_cases(
            CursorPaginationParams(limit=1),
            status=CaseStatus.NEW,
            search_term="Synthetic",
            sort="asc",
        )
    assert legacy.total_estimate == 3
    assert len(population_counts(legacy_queries)) == 1
    assert legacy.next_cursor is not None
    with capture_search_queries(session) as ui_queries:
        aggregates = await service.get_search_case_aggregates(
            status=CaseStatus.NEW, search_term="Synthetic"
        )
        first = await service.search_cases(
            CursorPaginationParams(limit=1),
            status=CaseStatus.NEW,
            search_term="Synthetic",
            sort="asc",
            include_count=False,
        )
        following = await service.search_cases(
            CursorPaginationParams(limit=1, cursor=legacy.next_cursor),
            status=CaseStatus.NEW,
            search_term="Synthetic",
            sort="asc",
            include_count=False,
        )
    assert len(population_counts(ui_queries)) == 1
    assert aggregates.total == aggregates.status_groups.new == 3
    assert first.total_estimate is following.total_estimate is None
    assert first.items[0].id == synthetic_search_cases.ids[0]
    assert following.items[0].id == synthetic_search_cases.ids[1]


async def test_minimal_projection_omits_payload_and_unused_related_reads(
    session: AsyncSession,
    svc_role: Role,
    synthetic_search_cases: SyntheticSearchCases,
) -> None:
    service = CasesService(session, svc_role)
    with capture_search_queries(session) as queries:
        page = await service.search_cases(
            CursorPaginationParams(limit=2), sort="asc", include_count=False
        )
    assert [item.id for item in page.items] == list(synthetic_search_cases.ids[:2])
    assert all(item.payload is None for item in page.items)
    assert all(
        item.assignee is not None and item.tags and item.dropdown_values
        for item in page.items
    )
    assert page.items[1].parent is not None
    assert page.items[1].parent.id == synthetic_search_cases.ids[0]
    statements = "\n".join(query.statement for query in queries)
    assert '"case".payload' not in statements
    assert '"case".description' not in statements
    assert "case_table_row" not in statements
    assert "oauth_account" not in statements
    assert "case_duration" not in statements
    option_queries = [
        query for query in queries if "case_dropdown_option." in query.statement
    ]
    assert len(option_queries) == 1
    assert "case_dropdown_option.definition_id IN" not in option_queries[0].statement
    assert all(
        item.dropdown_values[0].option_id == synthetic_search_cases.selected_option_id
        for item in page.items
    )


async def test_optional_payload_and_duration_hydration_excludes_lookahead_case(
    session: AsyncSession,
    svc_role: Role,
    synthetic_search_cases: SyntheticSearchCases,
) -> None:
    service = CasesService(session, svc_role)
    with capture_search_queries(session) as queries:
        page = await service.search_cases(
            CursorPaginationParams(limit=2),
            sort="asc",
            include_count=False,
            include_payload=True,
            include_durations=True,
        )
    assert len(page.items) == 2 and page.has_more
    assert all(
        item.payload and item.durations and item.durations[0].case_id == item.id
        for item in page.items
    )
    main_query = next(
        query for query in queries if "_tracecat_page_key_0" in query.statement
    )
    assert '"case".payload' not in main_query.statement
    payload_queries = [
        query for query in queries if '"case".payload' in query.statement
    ]
    assert len(payload_queries) == 1
    payload_case_ids = {
        value
        for value in payload_queries[0].parameters
        if value != svc_role.workspace_id
    }
    assert payload_case_ids == set(synthetic_search_cases.ids[:2])
    relationship_queries = [
        query for query in queries if "case_1.surrogate_id IN" in query.statement
    ]
    assert relationship_queries
    for query in relationship_queries:
        assert set(query.parameters) == set(synthetic_search_cases.surrogate_ids[:2])


async def test_disabled_addons_skip_dropdown_duration_and_hierarchy_queries(
    session: AsyncSession,
    svc_role: Role,
    synthetic_search_cases: SyntheticSearchCases,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"case_addons": False}),
    )
    service = CasesService(session, svc_role)
    with capture_search_queries(session) as queries:
        page = await service.search_cases(
            CursorPaginationParams(limit=2),
            sort="asc",
            include_count=False,
            include_durations=True,
        )
    assert all(
        not item.dropdown_values
        and item.durations is None
        and item.parent is None
        and item.num_sub_cases == 0
        for item in page.items
    )
    statements = "\n".join(query.statement for query in queries)
    assert "case_dropdown" not in statements
    assert "case_duration" not in statements
    assert "case_table_row" not in statements


async def test_deletion_between_page_read_and_hydration_does_not_read_unloaded_state(
    session: AsyncSession,
    svc_role: Role,
    synthetic_search_cases: SyntheticSearchCases,
) -> None:
    deleted = False

    def delete_before_hydration(state: ORMExecuteState) -> None:
        nonlocal deleted
        # The page has no ID-IN clause in this fixture; the first root Case
        # query with one is returned-page hydration. Perform a real SQL delete
        # at that boundary to reproduce a row disappearing between snapshots.
        if (
            not deleted
            and state.is_select
            and not state.is_relationship_load
            and state.bind_arguments.get("mapper") is Case.__mapper__
            and " IN (" in str(state.statement)
        ):
            state.session.connection().execute(
                delete(Case).where(Case.id.in_(synthetic_search_cases.ids[:2]))
            )
            deleted = True

    event.listen(session.sync_session, "do_orm_execute", delete_before_hydration)
    try:
        page = await CasesService(session, svc_role).search_cases(
            CursorPaginationParams(limit=2), sort="asc", include_count=False
        )
    finally:
        event.remove(session.sync_session, "do_orm_execute", delete_before_hydration)
    assert deleted
    assert page.items == []
    assert page.has_more and page.next_cursor is not None
    following = await CasesService(session, svc_role).search_cases(
        CursorPaginationParams(limit=2, cursor=page.next_cursor),
        sort="asc",
        include_count=False,
    )
    assert [item.id for item in following.items] == list(
        synthetic_search_cases.ids[2:4]
    )


async def test_list_count_opt_out_and_empty_page_are_compatible(
    session: AsyncSession, svc_role: Role
) -> None:
    service = CasesService(session, svc_role)
    with capture_search_queries(session) as queries:
        empty = await service.list_cases(limit=2, include_count=False)
    assert empty.items == [] and empty.total_estimate is None
    assert not population_counts(queries)
    assert len(queries) == 1
    counted = await service.list_cases(limit=2)
    assert counted.total_estimate == 0
    # Field validation must still run after the canonical query returns no rows.
    with pytest.raises(ValueError, match="Field case_id is a reserved field"):
        await service.query_cases(
            CaseSearchRequest(field_ids=["case_id"], include_count=False)
        )


async def test_canonical_result_flags_and_selected_page_hydration(
    session: AsyncSession, svc_role: Role, synthetic_search_cases: SyntheticSearchCases
) -> None:
    """Rows/fields/payload/durations and count do not affect matching or cursors."""
    service = CasesService(session, svc_role)
    await service.fields.create_field(
        CaseFieldCreate(name="risk_score", type=SqlType.INTEGER)
    )
    table = await service.tables.create_table(
        TableCreate(
            name="synthetic_search_rows",
            columns=[TableColumnCreate(name="value", type=SqlType.TEXT)],
        )
    )
    for case_id in synthetic_search_cases.ids:
        case = await session.scalar(select(Case).where(Case.id == case_id))
        assert case is not None
        await service.fields.upsert_field_values(case, {"risk_score": 80})
        row = await service.tables.insert_row(
            table, TableRowInsert(data={"value": "synthetic"})
        )
        assert isinstance(row["id"], uuid.UUID)
        session.add(
            CaseTableRow(
                workspace_id=service.workspace_id,
                case_id=case_id,
                table_id=table.id,
                row_id=row["id"],
            )
        )
    await session.flush()
    session.expunge_all()
    baseline = await service.query_cases(CaseSearchRequest(limit=2, sort="asc"))
    for include_rows, field_ids in (
        (False, None),
        (True, None),
        (False, ["risk_score"]),
        (True, ["risk_score"]),
    ):
        with capture_search_queries(session) as queries:
            response = await service.query_cases(
                CaseSearchRequest(
                    limit=2,
                    sort="asc",
                    include_rows=include_rows,
                    field_ids=field_ids,
                    include_payload=True,
                    include_durations=True,
                    include_count=False,
                )
            )
        assert [item.id for item in response.items] == [
            item.id for item in baseline.items
        ]
        assert response.next_cursor == baseline.next_cursor
        assert response.total_estimate is None
        assert not population_counts(queries)
        for item in response.items:
            assert bool(item.rows) is include_rows
            assert item.field_values == ({"risk_score": 80} if field_ids else None)
            assert item.payload is not None and item.durations
            # Pydantic's default timedelta JSON contract is an ISO 8601 string.
            assert isinstance(
                item.model_dump(mode="json")["durations"][0]["duration"], str
            )
        row_queries = [
            query for query in queries if "case_table_row.case_id IN" in query.statement
        ]
        assert bool(row_queries) is include_rows
        if row_queries:
            assert set(row_queries[0].parameters) == {
                service.workspace_id,
                *synthetic_search_cases.ids[:2],
            }
        field_queries = [
            query
            for query in queries
            if "risk_score" in query.statement and "case_id IN" in query.statement
        ]
        if field_ids:
            assert field_queries and set(field_queries[-1].parameters) == set(
                synthetic_search_cases.ids[:2]
            )


async def test_missing_physical_table_keeps_later_rows_fields_and_session_usable(
    session: AsyncSession, svc_role: Role, synthetic_search_cases: SyntheticSearchCases
) -> None:
    service = CasesService(session, svc_role)
    case_id = synthetic_search_cases.ids[0]
    await service.fields.create_field(
        CaseFieldCreate(name="risk_score", type=SqlType.INTEGER)
    )
    case = await session.scalar(select(Case).where(Case.id == case_id))
    assert case is not None
    await service.fields.upsert_field_values(case, {"risk_score": 80})
    missing = await service.tables.create_table(
        TableCreate(name="synthetic_missing_rows")
    )
    available = await service.tables.create_table(
        TableCreate(
            name="synthetic_available_rows",
            columns=[TableColumnCreate(name="value", type=SqlType.TEXT)],
        )
    )
    available_row = await service.tables.insert_row(
        available, TableRowInsert(data={"value": "synthetic available"})
    )
    available_row_id = available_row["id"]
    assert isinstance(available_row_id, uuid.UUID)
    missing_table_id = missing.id
    available_table_id = available.id
    session.add_all(
        [
            CaseTableRow(
                workspace_id=service.workspace_id,
                case_id=case_id,
                table_id=missing_table_id,
                row_id=uuid.uuid4(),
                created_at=datetime(2026, 3, 9, tzinfo=UTC),
            ),
            CaseTableRow(
                workspace_id=service.workspace_id,
                case_id=case_id,
                table_id=available_table_id,
                row_id=available_row_id,
                created_at=datetime(2026, 3, 8, tzinfo=UTC),
            ),
        ]
    )
    await session.flush()
    await session.execute(
        DropTable(
            SqlTable(missing.name, MetaData(), schema=service.tables._get_schema_name())
        )
    )
    session.expunge_all()
    with capture_search_queries(session) as queries:
        page = await service.query_cases(
            CaseSearchRequest(
                case_id=case_id,
                include_rows=True,
                field_ids=["risk_score"],
                include_count=False,
            )
        )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.field_values == {"risk_score": 80}
    assert item.rows is not None
    assert [row.table_id for row in item.rows] == [missing_table_id, available_table_id]
    missing_row, available_link = item.rows
    assert not missing_row.is_row_available and missing_row.row_data is None
    assert available_link.is_row_available
    assert available_link.row_data is not None
    assert available_link.row_data["value"] == "synthetic available"
    physical_reads = [
        query.statement
        for query in queries
        if "synthetic_missing_rows" in query.statement
        or "synthetic_available_rows" in query.statement
    ]
    assert len(physical_reads) == 2
    assert "synthetic_missing_rows" in physical_reads[0]
    assert "synthetic_available_rows" in physical_reads[1]
    assert await session.scalar(select(1)) == 1


async def test_unrelated_row_read_error_is_rethrown_and_savepoint_recovers(
    session: AsyncSession, svc_role: Role, synthetic_search_cases: SyntheticSearchCases
) -> None:
    service = CasesService(session, svc_role)
    table = await service.tables.create_table(
        TableCreate(
            name="synthetic_invalid_column_rows",
            columns=[TableColumnCreate(name="value", type=SqlType.TEXT)],
        )
    )
    row = await service.tables.insert_row(
        table, TableRowInsert(data={"value": "synthetic"})
    )
    row_id = row["id"]
    assert isinstance(row_id, uuid.UUID)
    session.add(
        CaseTableRow(
            workspace_id=service.workspace_id,
            case_id=synthetic_search_cases.ids[0],
            table_id=table.id,
            row_id=row_id,
        )
    )
    await session.flush()
    physical = SqlTable(
        table.name, MetaData(), schema=service.tables._get_schema_name()
    )
    # Keep metadata unchanged while simulating another physical-schema failure.
    await session.execute(
        text(f'ALTER TABLE "{physical.schema}"."{physical.name}" DROP COLUMN value')
    )
    session.expunge_all()
    with pytest.raises(DBAPIError):
        await service.query_cases(
            CaseSearchRequest(case_id=synthetic_search_cases.ids[0], include_rows=True)
        )
    assert await session.scalar(select(1)) == 1
