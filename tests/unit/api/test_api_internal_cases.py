import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.cases import internal_router as internal_cases_router
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.db.models import Case, Workspace


@pytest.fixture
def mock_internal_case(test_workspace: Workspace) -> Case:
    case = Case(
        workspace_id=test_workspace.id,
        summary="Internal case summary",
        description="Internal case description",
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
        payload={"source": "test"},
        assignee_id=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    case.id = uuid.uuid4()
    case.case_number = 1
    case.tags = []
    case.assignee = None
    case.dropdown_values = []
    return case


def _build_case_row(case_id: uuid.UUID) -> CaseTableRowRead:
    return CaseTableRowRead(
        id=uuid.uuid4(),
        case_id=case_id,
        table_id=uuid.uuid4(),
        table_name="table_name",
        row_id=uuid.uuid4(),
        row_data={"value": "row data"},
        is_row_available=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_internal_get_case_include_rows_hydrates_rows(
    client: TestClient, test_admin_role: Role, mock_internal_case: Case
) -> None:
    row = _build_case_row(mock_internal_case.id)
    with (
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
        patch.object(
            internal_cases_router,
            "_list_case_dropdown_values",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            internal_cases_router,
            "_list_case_rows",
            new=AsyncMock(return_value=[row]),
        ) as mock_list_rows,
    ):
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service.fields = AsyncMock()
        mock_service.fields.get_fields.return_value = {}
        mock_service.fields.list_fields.return_value = []
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/internal/cases/{mock_internal_case.id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "include_rows": "true",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["id"] == str(row.id)
    assert mock_list_rows.await_count == 1


@pytest.mark.anyio
async def test_internal_update_case_include_rows_hydrates_rows(
    client: TestClient, test_admin_role: Role, mock_internal_case: Case
) -> None:
    row = _build_case_row(mock_internal_case.id)
    with (
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
        patch.object(
            internal_cases_router,
            "_list_case_dropdown_values",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            internal_cases_router,
            "_list_case_rows",
            new=AsyncMock(return_value=[row]),
        ) as mock_list_rows,
    ):
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        updated_case = mock_internal_case
        updated_case.summary = "Updated summary"
        mock_service.update_case.return_value = updated_case
        mock_service.fields = AsyncMock()
        mock_service.fields.get_fields.return_value = {}
        mock_service.fields.list_fields.return_value = []
        mock_service_cls.return_value = mock_service

        response = client.patch(
            f"/internal/cases/{mock_internal_case.id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "include_rows": "true",
            },
            json={"summary": "Updated summary"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["summary"] == "Updated summary"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["id"] == str(row.id)
    assert mock_list_rows.await_count == 1
