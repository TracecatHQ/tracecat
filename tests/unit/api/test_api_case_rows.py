import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.cases import search_service
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows import internal_router as internal_case_rows_router
from tracecat.cases.rows import router as case_rows_router
from tracecat.cases.rows.hydration import CaseRowHydrator
from tracecat.cases.rows.schemas import (
    MAX_CASE_ROW_BATCH_SIZE,
    CaseLinkedTableRead,
    CaseTableRowBatchLink,
    CaseTableRowBatchLinkResponse,
    CaseTableRowBatchUnlink,
    CaseTableRowBatchUnlinkResponse,
    CaseTableRowLinkCreate,
)
from tracecat.cases.rows.service import MAX_LINKED_ROWS_PER_CASE
from tracecat.cases.schemas import CaseReadMinimal
from tracecat.cases.search_service import CaseSearchService
from tracecat.cases.service import CasesService
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse, Page
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnRead


def _build_case_read(case_id: uuid.UUID) -> CaseReadMinimal:
    return CaseReadMinimal(
        id=case_id,
        short_id="CASE-0001",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        summary="Case",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
        assignee=None,
        tags=[],
        dropdown_values=[],
        num_tasks_completed=0,
        num_tasks_total=0,
        num_sub_cases=0,
    )


def _duplicate_case_row_link_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO case_table_row ...",
        {},
        Exception(
            'duplicate key value violates unique constraint "uq_case_table_row_link"'
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("error_boundary", ["service", "hydrator"])
async def test_list_cases_include_rows_errors_are_sanitized(
    client: TestClient, test_admin_role: Role, error_boundary: str
) -> None:
    failure = AsyncMock(side_effect=RuntimeError("sensitive error details"))
    target, method = (
        (CasesService, "list_cases")
        if error_boundary == "service"
        else (CaseRowHydrator, "hydrate_case_rows")
    )
    with (
        patch.object(CasesService, "has_entitlement", return_value=False),
        patch.object(CaseSearchService, "prepare_query", return_value=[]),
        patch.object(search_service, "paginate", return_value=Page(items=[])),
        patch.object(target, method, new=failure),
    ):
        response = client.get(
            "/cases",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "include_rows": "true",
                "include_count": "false",
            },
        )
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "Failed to retrieve cases"
    if error_boundary == "hydrator":
        failure.assert_awaited_once_with(case_ids=[], include_row_data=True)
    else:
        failure.assert_awaited_once()
        assert failure.call_args.kwargs["include_rows"] is True


_ROW_LIMIT_ERROR = f"A case can have at most {MAX_LINKED_ROWS_PER_CASE} linked rows"
_CASE_ID = uuid.uuid4()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route", "kwargs", "service_method", "error", "status_code", "detail"),
    [
        pytest.param(
            "link_case_row",
            {
                "case_id": _CASE_ID,
                "params": CaseTableRowLinkCreate(
                    table_id=uuid.uuid4(), row_id=uuid.uuid4()
                ),
            },
            "link_row",
            ValueError(_ROW_LIMIT_ERROR),
            status.HTTP_400_BAD_REQUEST,
            _ROW_LIMIT_ERROR,
            id="link-value-error",
        ),
        pytest.param(
            "list_case_linked_tables",
            {"case_id": _CASE_ID},
            "get_case_or_raise",
            TracecatNotFoundError("Case not found"),
            status.HTTP_404_NOT_FOUND,
            "Case not found",
            id="linked-tables-missing-case",
        ),
        pytest.param(
            "batch_link_case_rows",
            {
                "case_id": _CASE_ID,
                "params": CaseTableRowBatchLink(
                    table_id=uuid.uuid4(), row_ids=[uuid.uuid4()]
                ),
            },
            "get_case_or_raise",
            TracecatNotFoundError("Case not found"),
            status.HTTP_404_NOT_FOUND,
            "Case not found",
            id="batch-link-missing-case",
        ),
        pytest.param(
            "batch_unlink_case_rows",
            {
                "case_id": _CASE_ID,
                "params": CaseTableRowBatchUnlink(
                    table_id=uuid.uuid4(), row_ids=[uuid.uuid4()]
                ),
            },
            "get_case_or_raise",
            TracecatNotFoundError("Case not found"),
            status.HTTP_404_NOT_FOUND,
            "Case not found",
            id="batch-unlink-missing-case",
        ),
        pytest.param(
            "batch_link_case_rows",
            {
                "case_id": _CASE_ID,
                "params": CaseTableRowBatchLink(
                    table_id=uuid.uuid4(),
                    row_ids=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
                ),
            },
            "link_rows",
            TracecatNotFoundError("2 of 3 rows not found in table t"),
            status.HTTP_404_NOT_FOUND,
            "2 of 3 rows not found in table t",
            id="batch-link-missing-rows",
        ),
        pytest.param(
            "batch_link_case_rows",
            {
                "case_id": _CASE_ID,
                "params": CaseTableRowBatchLink(
                    table_id=uuid.uuid4(), row_ids=[uuid.uuid4()]
                ),
            },
            "link_rows",
            ValueError(_ROW_LIMIT_ERROR),
            status.HTTP_400_BAD_REQUEST,
            _ROW_LIMIT_ERROR,
            id="batch-link-row-limit",
        ),
        pytest.param(
            "list_linked_cases",
            {
                "table_id": uuid.uuid4(),
                "row_id": uuid.uuid4(),
                "limit": 10,
                "cursor": None,
                "reverse": False,
                "order_by": None,
                "sort": None,
                "exclude_case_id": None,
            },
            "list_linked_cases",
            TracecatNotFoundError("Table not found"),
            status.HTTP_404_NOT_FOUND,
            None,
            id="linked-cases-missing-table",
        ),
    ],
)
async def test_case_row_route_errors_map_to_http_exceptions(
    test_admin_role: Role,
    route: str,
    kwargs: dict[str, object],
    service_method: str,
    error: Exception,
    status_code: int,
    detail: str | None,
) -> None:
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        getattr(mock_service, service_method).side_effect = error
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await getattr(case_rows_router, route)(
                role=test_admin_role, session=AsyncMock(), **kwargs
            )

    assert exc_info.value.status_code == status_code
    if detail is not None:
        assert exc_info.value.detail == detail


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("missing_case", "link_error", "status_code", "detail"),
    [
        pytest.param(
            True, None, status.HTTP_404_NOT_FOUND, "Case not found", id="missing-case"
        ),
        pytest.param(
            False,
            ValueError("A case can link rows from at most 10 tables"),
            status.HTTP_400_BAD_REQUEST,
            "A case can link rows from at most 10 tables",
            id="value-error",
        ),
        pytest.param(
            False,
            _duplicate_case_row_link_error(),
            status.HTTP_409_CONFLICT,
            {
                "code": "CASE_ROW_ALREADY_LINKED",
                "message": "This table row is already linked to the case.",
            },
            id="duplicate-link",
        ),
    ],
)
async def test_internal_link_case_row_errors_map_to_http_status(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    missing_case: bool,
    link_error: Exception | None,
    status_code: int,
    detail: object,
) -> None:
    with patch.object(
        internal_case_rows_router, "CaseTableRowsService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        if missing_case:
            mock_service.get_case_or_raise.side_effect = TracecatNotFoundError(
                "Case not found"
            )
        else:
            mock_service.get_case_or_raise.return_value = MagicMock()
            mock_service.link_row.side_effect = link_error
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            f"/internal/cases/{uuid.uuid4()}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(uuid.uuid4()), "row_id": str(uuid.uuid4())},
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail


@pytest.mark.anyio
async def test_link_case_row_returns_409_for_duplicate_link(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    session = AsyncMock()
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_row.side_effect = _duplicate_case_row_link_error()
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.link_case_row(
                role=test_admin_role,
                session=session,
                case_id=case_id,
                params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
            )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == {
        "code": "CASE_ROW_ALREADY_LINKED",
        "message": "This table row is already linked to the case.",
    }
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_list_case_linked_tables_returns_columns(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    table_id = uuid.uuid4()
    column_id = uuid.uuid4()
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.list_linked_tables.return_value = [
            CaseLinkedTableRead(
                table_id=table_id,
                table_name="alerts",
                row_count=2,
                columns=[
                    TableColumnRead(id=column_id, name="value", type=SqlType.TEXT)
                ],
            )
        ]
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/cases/{uuid.uuid4()}/rows/tables",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    linked_table = response.json()[0]
    assert linked_table["table_id"] == str(table_id)
    assert linked_table["columns"] == [
        {
            "id": str(column_id),
            "name": "value",
            "type": SqlType.TEXT.value,
            "nullable": True,
            "default": None,
            "is_index": False,
            "options": None,
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "row_ids"),
    [
        ("batch-link", []),
        ("batch-link", [str(uuid.uuid4())] * (MAX_CASE_ROW_BATCH_SIZE + 1)),
        ("batch-unlink", []),
    ],
)
async def test_batch_case_rows_validates_row_ids(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    row_ids: list[str],
) -> None:
    response = client.post(
        f"/cases/{uuid.uuid4()}/rows/{path}",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={"table_id": str(uuid.uuid4()), "row_ids": row_ids},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_batch_link_case_rows_accepts_a_full_batch(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    row_ids = [str(uuid.uuid4()) for _ in range(MAX_CASE_ROW_BATCH_SIZE)]
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_rows.return_value = CaseTableRowBatchLinkResponse(
            linked_count=MAX_CASE_ROW_BATCH_SIZE,
            already_linked_count=0,
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/cases/{uuid.uuid4()}/rows/batch-link",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(uuid.uuid4()), "row_ids": row_ids},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["linked_count"] == MAX_CASE_ROW_BATCH_SIZE


@pytest.mark.anyio
async def test_batch_link_case_rows_returns_service_counts(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    params = CaseTableRowBatchLink(table_id=uuid.uuid4(), row_ids=[uuid.uuid4()])
    expected = CaseTableRowBatchLinkResponse(
        linked_count=2,
        already_linked_count=1,
    )
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_rows.return_value = expected
        mock_service_cls.return_value = mock_service

        result = await case_rows_router.batch_link_case_rows(
            role=test_admin_role,
            session=AsyncMock(),
            case_id=case_id,
            params=params,
        )

    assert result is expected


@pytest.mark.anyio
async def test_batch_unlink_case_rows_wraps_service_count(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    params = CaseTableRowBatchUnlink(table_id=uuid.uuid4(), row_ids=[uuid.uuid4()])
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.unlink_rows.return_value = 3
        mock_service_cls.return_value = mock_service

        result = await case_rows_router.batch_unlink_case_rows(
            role=test_admin_role,
            session=AsyncMock(),
            case_id=case_id,
            params=params,
        )

    assert result == CaseTableRowBatchUnlinkResponse(unlinked_count=3)


@pytest.mark.anyio
async def test_list_case_rows_overlong_search_term_returns_422(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/cases/{uuid.uuid4()}/rows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "table_id": str(uuid.uuid4()),
                "search_term": "a" * 1001,
                "search_column": "name",
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_service.list_rows.assert_not_awaited()


@pytest.mark.anyio
async def test_list_linked_cases_returns_service_page(
    client: TestClient, test_admin_role: Role
) -> None:
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    case_id = uuid.uuid4()
    excluded_id = uuid.uuid4()
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.list_linked_cases.return_value = CursorPaginatedResponse(
            items=[_build_case_read(case_id)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/tables/{table_id}/rows/{row_id}/cases",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "limit": 5,
                "exclude_case_id": str(excluded_id),
                "order_by": "severity",
                "sort": "desc",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert [item["id"] for item in response.json()["items"]] == [str(case_id)]
    kwargs = mock_service.list_linked_cases.await_args.kwargs
    assert kwargs["table_id"] == table_id
    assert kwargs["row_id"] == row_id
    assert kwargs["exclude_case_id"] == excluded_id
    assert kwargs["params"].limit == 5
    assert kwargs["order_by"] == "severity"
    assert kwargs["sort"] == "desc"


@pytest.mark.anyio
async def test_internal_list_linked_cases_resolves_short_exclude_id(
    test_admin_role: Role,
) -> None:
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    excluded_id = uuid.uuid4()
    with (
        patch.object(internal_case_rows_router, "CasesService") as mock_cases_cls,
        patch.object(
            internal_case_rows_router, "CaseTableRowsService"
        ) as mock_service_cls,
    ):
        mock_cases = AsyncMock()
        mock_cases.resolve_case_id.return_value = excluded_id
        mock_cases_cls.return_value = mock_cases
        mock_service = AsyncMock()
        mock_service_cls.return_value = mock_service

        await internal_case_rows_router.list_linked_cases(
            role=test_admin_role,
            session=AsyncMock(),
            table_id=table_id,
            row_id=row_id,
            limit=10,
            cursor=None,
            reverse=False,
            order_by=None,
            sort=None,
            exclude_case_id="CASE-0042",
        )

    mock_cases.resolve_case_id.assert_awaited_once_with("CASE-0042")
    assert (
        mock_service.list_linked_cases.await_args.kwargs["exclude_case_id"]
        == excluded_id
    )
