import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.cases import router as cases_router
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows import internal_router as internal_case_rows_router
from tracecat.cases.rows import router as case_rows_router
from tracecat.cases.rows.schemas import CaseTableRowLinkCreate
from tracecat.cases.schemas import CaseReadMinimal
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse


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
    with patch.object(case_rows_router, "CaseTableRowsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case_or_raise.return_value = MagicMock()
        mock_service.link_row.side_effect = ValueError(
            "A case can have at most 200 linked rows"
        )
        mock_service_cls.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await case_rows_router.link_case_row(
                role=test_admin_role,
                session=AsyncMock(),
                case_id=case_id,
                params=CaseTableRowLinkCreate(table_id=table_id, row_id=row_id),
            )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "A case can have at most 200 linked rows"


@pytest.mark.anyio
async def test_internal_link_case_row_returns_404_for_missing_case(
    client: TestClient, test_admin_role: Role
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

        response = client.post(
            f"/internal/cases/{case_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(table_id), "row_id": str(row_id)},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Case not found"


@pytest.mark.anyio
async def test_internal_link_case_row_returns_400_for_value_error(
    client: TestClient, test_admin_role: Role
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

        response = client.post(
            f"/internal/cases/{case_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"table_id": str(table_id), "row_id": str(row_id)},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "A case can link rows from at most 10 tables"
