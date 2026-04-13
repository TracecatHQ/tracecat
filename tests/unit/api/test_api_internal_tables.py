"""HTTP-level tests for internal tables API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.db.models import Table, Workspace
from tracecat.tables import internal_router as internal_tables_router


@pytest.fixture
def mock_table(test_workspace: Workspace) -> Table:
    table = Table(
        id=uuid.UUID("ffffffff-ffff-4fff-ffff-ffffffffffff"),
        workspace_id=test_workspace.id,
        name="test_table",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    table.columns = []
    return table


@pytest.mark.anyio
async def test_internal_insert_table_row_invalid_numeric_value_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.insert_row.side_effect = ValueError("Invalid numeric value: 'abc'")
        MockService.return_value = mock_svc

        response = client.post(
            f"/internal/tables/{mock_table.name}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"data": {"score": "abc"}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid numeric value: 'abc'"


@pytest.mark.anyio
async def test_internal_update_table_row_invalid_integer_value_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.update_row.side_effect = ValueError("Invalid integer value: '1.5'")
        MockService.return_value = mock_svc

        response = client.patch(
            f"/internal/tables/{mock_table.name}/rows/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"data": {"attempts": "1.5"}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid integer value: '1.5'"
