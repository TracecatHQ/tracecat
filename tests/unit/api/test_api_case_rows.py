import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.cases import router as cases_router
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows import internal_router as internal_case_rows_router
from tracecat.cases.rows import router as case_rows_router
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
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse
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
async def test_list_cases_include_rows_hydration_error_is_sanitized(
    client: TestClient, test_admin_role: Role
) -> None:
    case_id = uuid.uuid4()
    with (
        patch.object(cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(cases_router, "CaseTableRowsService") as mock_rows_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.list_cases.return_value = CursorPaginatedResponse(
            items=[_build_case_read(case_id)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_cases_service_cls.return_value = mock_cases_service

        mock_rows_service = AsyncMock()
        mock_rows_service.hydrate_case_rows.side_effect = RuntimeError(
            "sensitive error details"
        )
        mock_rows_service_cls.return_value = mock_rows_service

        response = client.get(
            "/cases",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "include_rows": "true",
            },
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "Failed to hydrate linked rows"


@pytest.mark.anyio
async def test_link_case_row_returns_400_for_value_error(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    expected_error = f"A case can have at most {MAX_LINKED_ROWS_PER_CASE} linked rows"
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_row.side_effect = ValueError(expected_error)
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.link_case_row(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
                params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
            )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == expected_error


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
async def test_internal_link_case_row_returns_404_for_missing_case(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    case_id = uuid.uuid4()
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    with patch.object(
        internal_case_rows_router, "CaseTableRowsService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.side_effect = TracecatNotFoundError(
            "Case not found"
        )
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            f"/internal/cases/{case_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(table_id), "row_id": str(row_id)},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Case not found"


@pytest.mark.anyio
async def test_internal_link_case_row_returns_400_for_value_error(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    case_id = uuid.uuid4()
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    with patch.object(
        internal_case_rows_router, "CaseTableRowsService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_row.side_effect = ValueError(
            "A case can link rows from at most 10 tables"
        )
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            f"/internal/cases/{case_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(table_id), "row_id": str(row_id)},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "A case can link rows from at most 10 tables"


@pytest.mark.anyio
async def test_internal_link_case_row_returns_409_for_duplicate_link(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    case_id = uuid.uuid4()
    table_id = uuid.uuid4()
    row_id = uuid.uuid4()
    with patch.object(
        internal_case_rows_router, "CaseTableRowsService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_row.side_effect = _duplicate_case_row_link_error()
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            f"/internal/cases/{case_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(table_id), "row_id": str(row_id)},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == {
        "code": "CASE_ROW_ALREADY_LINKED",
        "message": "This table row is already linked to the case.",
    }


@pytest.mark.anyio
async def test_list_case_linked_tables_returns_404_for_missing_case(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.side_effect = TracecatNotFoundError(
            "Case not found"
        )
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.list_case_linked_tables(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
            )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Case not found"


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
async def test_batch_link_case_rows_returns_404_for_missing_case(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    params = CaseTableRowBatchLink(table_id=uuid.uuid4(), row_ids=[uuid.uuid4()])
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.side_effect = TracecatNotFoundError(
            "Case not found"
        )
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.batch_link_case_rows(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
                params=params,
            )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Case not found"


@pytest.mark.anyio
async def test_batch_unlink_case_rows_returns_404_for_missing_case(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    params = CaseTableRowBatchUnlink(table_id=uuid.uuid4(), row_ids=[uuid.uuid4()])
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.side_effect = TracecatNotFoundError(
            "Case not found"
        )
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.batch_unlink_case_rows(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
                params=params,
            )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Case not found"


@pytest.mark.anyio
async def test_batch_link_case_rows_returns_404_for_missing_rows(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    params = CaseTableRowBatchLink(
        table_id=uuid.uuid4(),
        row_ids=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
    )
    expected_error = "2 of 3 rows not found in table t"
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_rows.side_effect = TracecatNotFoundError(expected_error)
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.batch_link_case_rows(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
                params=params,
            )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == expected_error


@pytest.mark.anyio
async def test_batch_link_case_rows_returns_400_for_row_limit(
    test_admin_role: Role,
) -> None:
    case_id = uuid.uuid4()
    params = CaseTableRowBatchLink(table_id=uuid.uuid4(), row_ids=[uuid.uuid4()])
    expected_error = f"A case can have at most {MAX_LINKED_ROWS_PER_CASE} linked rows"
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_rows.side_effect = ValueError(expected_error)
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.batch_link_case_rows(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
                params=params,
            )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == expected_error


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
