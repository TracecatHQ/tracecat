"""HTTP-level tests for tables API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import DuplicateTableError
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from tracecat.auth.types import Role
from tracecat.db.models import Table, Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse
from tracecat.tables import router as tables_router
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableRowRead


@pytest.fixture
def mock_table(test_workspace: Workspace) -> Table:
    """Create a mock table DB object."""
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
async def test_list_tables_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test GET /tables returns list of tables."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_tables.return_value = [mock_table]
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/tables",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "test_table"


@pytest.mark.anyio
async def test_create_table_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /tables creates a new table."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_table.return_value = None
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/tables",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "name": "new_table",
                "description": "New test table",
                "columns": [
                    {"name": "id", "type": SqlType.TEXT.value},
                    {"name": "value", "type": SqlType.TEXT.value},
                ],
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_create_table_duplicate(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /tables with duplicate name returns 409."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        # Create a nested exception chain to match the actual code
        duplicate_error = DuplicateTableError("Table already exists")
        programming_error = ProgrammingError("", {}, duplicate_error)
        programming_error.__cause__ = duplicate_error
        mock_svc.create_table.side_effect = programming_error
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/tables",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "name": "duplicate_table",
                "description": "Duplicate table",
                "columns": [{"name": "id", "type": SqlType.TEXT.value}],
            },
        )

        # Should return 409
        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_get_table_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test GET /tables/{table_id} returns table details."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.get_index.return_value = []
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.get(
            f"/tables/{table_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "test_table"
        assert "columns" in data


@pytest.mark.anyio
async def test_get_table_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /tables/{table_id} with non-existent ID returns 404."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.side_effect = TracecatNotFoundError("Table not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.get(
            f"/tables/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_table_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test PATCH /tables/{table_id} updates table."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.update_table.return_value = None
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.patch(
            f"/tables/{table_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"description": "Updated description"},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_table_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test DELETE /tables/{table_id} deletes table."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_table.return_value = None
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.delete(
            f"/tables/{table_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_insert_table_row_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test POST /tables/{table_id}/rows inserts a row."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.insert_row.return_value = None
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.post(
            f"/tables/{table_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"data": {"id": "row-1", "value": "test"}},
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_insert_table_rows_batch_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test POST /tables/{table_id}/rows/batch inserts multiple rows."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.batch_insert_rows.return_value = 2
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.post(
            f"/tables/{table_id}/rows/batch",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "rows": [
                    {"id": "row-1", "value": "test1"},
                    {"id": "row-2", "value": "test2"},
                ]
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["rows_inserted"] == 2


@pytest.mark.anyio
async def test_list_table_rows_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test GET /tables/{table_id}/rows returns table rows."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        row_id_1 = uuid.uuid4()
        row_id_2 = uuid.uuid4()
        mock_rows = [
            TableRowRead(
                id=row_id_1,
                value="test1",  # pyright: ignore[reportCallIssue]
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
            TableRowRead(
                id=row_id_2,
                value="test2",  # pyright: ignore[reportCallIssue]
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ]
        mock_response = CursorPaginatedResponse(
            items=mock_rows,
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.get_table.return_value = mock_table
        mock_svc.list_rows.return_value = mock_response
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.get(
            f"/tables/{table_id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["value"] == "test1"
