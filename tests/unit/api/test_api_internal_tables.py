"""HTTP-level tests for internal tables API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import DuplicateColumnError, DuplicateTableError
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from tracecat.auth.types import Role
from tracecat.db.models import Table, TableColumn, Workspace
from tracecat.tables import internal_router as internal_tables_router


def _programming_error(cause: BaseException) -> ProgrammingError:
    error = ProgrammingError("", {}, cause)
    error.__cause__ = cause
    return error


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


@pytest.fixture
def mock_table_column(mock_table: Table) -> TableColumn:
    column = TableColumn(
        id=uuid.UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee"),
        table_id=mock_table.id,
        name="score",
        type="NUMERIC",
        nullable=True,
        default=None,
    )
    column.table = mock_table
    mock_table.columns = [column]
    return column


@pytest.mark.anyio
async def test_internal_update_table_returns_metadata(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_table.name = "indicators_v2"
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.update_table.return_value = mock_table
        mock_svc.get_index.return_value = set()
        MockService.return_value = mock_svc

        response = client.patch(
            "/internal/tables/indicators",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "indicators_v2"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "id": str(mock_table.id),
        "name": "indicators_v2",
        "columns": [],
    }


@pytest.mark.anyio
async def test_internal_update_table_duplicate_name_returns_409(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.update_table.side_effect = _programming_error(
            DuplicateTableError("relation already exists: internal details")
        )
        MockService.return_value = mock_svc

        response = client.patch(
            "/internal/tables/indicators",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "indicators_v2"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Table already exists"


@pytest.mark.anyio
async def test_internal_create_column_returns_refreshed_metadata(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
    mock_table_column: TableColumn,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.create_column.return_value = mock_table_column
        mock_svc.get_table.return_value = mock_table
        mock_svc.get_index.return_value = {"score"}
        MockService.return_value = mock_svc

        response = client.post(
            f"/internal/tables/{mock_table.name}/columns",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "score", "type": "NUMERIC"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "id": str(mock_table.id),
        "name": mock_table.name,
        "columns": [
            {
                "id": str(mock_table_column.id),
                "name": "score",
                "type": "NUMERIC",
                "nullable": True,
                "default": None,
                "is_index": True,
                "options": None,
            }
        ],
    }
    mock_svc.get_table.assert_awaited_once_with(
        mock_table.id,
        populate_existing=True,
    )


@pytest.mark.anyio
async def test_internal_create_column_duplicate_name_returns_409(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.create_column.side_effect = _programming_error(
            DuplicateColumnError("column already exists: internal details")
        )
        MockService.return_value = mock_svc

        response = client.post(
            f"/internal/tables/{mock_table.name}/columns",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "score", "type": "NUMERIC"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Column already exists"


@pytest.mark.anyio
async def test_internal_create_column_unexpected_db_error_is_sanitized(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.create_column.side_effect = _programming_error(
            RuntimeError("raw database details")
        )
        MockService.return_value = mock_svc

        response = client.post(
            f"/internal/tables/{mock_table.name}/columns",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "score", "type": "NUMERIC"},
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"] == "An error occurred while creating the column"


@pytest.mark.anyio
async def test_internal_update_column_404s_when_column_missing(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        MockService.return_value = mock_svc

        response = client.patch(
            f"/internal/tables/{mock_table.name}/columns/missing",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"nullable": False},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert (
        response.json()["detail"] == "Column 'missing' not found in table 'test_table'"
    )


@pytest.mark.anyio
async def test_internal_update_column_normalizes_path_name(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
    mock_table_column: TableColumn,
) -> None:
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.update_column.return_value = mock_table_column
        mock_svc.get_table.return_value = mock_table
        mock_svc.get_index.return_value = set()
        MockService.return_value = mock_svc

        response = client.patch(
            f"/internal/tables/{mock_table.name}/columns/Score",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"nullable": False},
        )

    assert response.status_code == status.HTTP_200_OK
    mock_svc.update_column.assert_awaited_once()
    assert mock_svc.update_column.await_args.args[0] is mock_table_column


@pytest.mark.anyio
async def test_internal_delete_column_normalizes_path_name(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
    mock_table_column: TableColumn,
) -> None:
    refreshed_table = Table(
        id=mock_table.id,
        workspace_id=mock_table.workspace_id,
        name=mock_table.name,
        created_at=mock_table.created_at,
        updated_at=mock_table.updated_at,
    )
    refreshed_table.columns = []
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.delete_column.return_value = None
        mock_svc.get_table.return_value = refreshed_table
        mock_svc.get_index.return_value = set()
        MockService.return_value = mock_svc

        response = client.delete(
            f"/internal/tables/{mock_table.name}/columns/Score",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    mock_svc.delete_column.assert_awaited_once_with(mock_table_column)


@pytest.mark.anyio
async def test_internal_delete_column_returns_refreshed_metadata(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
    mock_table_column: TableColumn,
) -> None:
    refreshed_table = Table(
        id=mock_table.id,
        workspace_id=mock_table.workspace_id,
        name=mock_table.name,
        created_at=mock_table.created_at,
        updated_at=mock_table.updated_at,
    )
    refreshed_table.columns = []
    with patch.object(internal_tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table_by_name.return_value = mock_table
        mock_svc.delete_column.return_value = None
        mock_svc.get_table.return_value = refreshed_table
        mock_svc.get_index.return_value = set()
        MockService.return_value = mock_svc

        response = client.delete(
            f"/internal/tables/{mock_table.name}/columns/{mock_table_column.name}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "id": str(mock_table.id),
        "name": mock_table.name,
        "columns": [],
    }
    mock_svc.get_table.assert_awaited_once_with(
        mock_table.id,
        populate_existing=True,
    )


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
