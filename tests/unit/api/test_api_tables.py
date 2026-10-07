"""HTTP-level tests for tables API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import DuplicateTableError
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tracecat.auth.types import Role
from tracecat.db.models import Table, TableColumn, Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse
from tracecat.tables import router as tables_router
from tracecat.tables.enums import SqlType
from tracecat.tables.exceptions import TableRowError
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
        assert data[0]["created_at"] == "2024-01-01T00:00:00Z"
        assert data[0]["updated_at"] == "2024-01-01T00:00:00Z"


@pytest.mark.anyio
async def test_create_table_success(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Test POST /tables creates a new table and returns it."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_table.return_value = mock_table
        mock_svc.get_table.return_value = mock_table
        mock_svc.get_index.return_value = []
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
        data = response.json()
        assert data["id"] == str(mock_table.id)
        assert data["name"] == "test_table"


@pytest.mark.anyio
async def test_create_column_with_is_index_returns_indexed_column(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    column = TableColumn(
        id=uuid.UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeef"),
        table_id=mock_table.id,
        name="email",
        type=SqlType.TEXT.value,
        nullable=False,
        default=None,
        options=None,
    )
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.create_column.return_value = column
        mock_svc.get_index.return_value = ["email"]
        MockService.return_value = mock_svc

        response = client.post(
            f"/tables/{mock_table.id}/columns",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "name": "email",
                "type": SqlType.TEXT.value,
                "nullable": False,
                "is_index": True,
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["id"] == str(column.id)
        assert data["is_index"] is True
        assert mock_svc.create_column.await_args is not None
        assert mock_svc.create_column.await_args.args[1].is_index is True


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
    """Test PATCH /tables/{table_id} updates table and returns it."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.update_table.return_value = None
        mock_svc.get_index.return_value = []
        MockService.return_value = mock_svc

        # Make request
        table_id = str(mock_table.id)
        response = client.patch(
            f"/tables/{table_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"description": "Updated description"},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == str(mock_table.id)


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
    """Test POST /tables/{table_id}/rows inserts a row and returns it."""
    row_id = uuid.uuid4()
    now = datetime(2024, 1, 1, tzinfo=UTC)
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.insert_row.return_value = {
            "id": row_id,
            "created_at": now,
            "updated_at": now,
            "value": "test",
        }
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
        data = response.json()
        assert data["id"] == str(row_id)
        assert data["value"] == "test"


@pytest.mark.anyio
async def test_insert_table_row_invalid_numeric_value_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Invalid numeric row input should be surfaced as a client error."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.insert_row.side_effect = ValueError("Invalid numeric value: 'abc'")
        MockService.return_value = mock_svc

        response = client.post(
            f"/tables/{mock_table.id}/rows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"data": {"score": "abc"}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid numeric value: 'abc'"


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


@pytest.mark.anyio
async def test_update_table_row_invalid_integer_value_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    """Invalid integer row input should be surfaced as a client error."""
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.update_row.side_effect = ValueError("Invalid integer value: '1.5'")
        MockService.return_value = mock_svc

        response = client.patch(
            f"/tables/{mock_table.id}/rows/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"data": {"attempts": "1.5"}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid integer value: '1.5'"


@pytest.mark.anyio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "error,expected_status",
    [
        (
            TableRowError(
                "missing_required_column",
                "Required column 'record_key' is missing.",
                column="record_key",
            ),
            400,
        ),
        (
            TableRowError(
                "null_not_allowed",
                "Column 'record_key' cannot be null.",
                column="record_key",
            ),
            400,
        ),
        (
            TableRowError(
                "unknown_column", "Column 'unknown' does not exist.", column="unknown"
            ),
            400,
        ),
        (
            TableRowError(
                "invalid_value",
                "Column 'attempts' requires a valid INTEGER value.",
                column="attempts",
            ),
            400,
        ),
        (
            TableRowError(
                "duplicate_value", "A value already exists in a unique column."
            ),
            409,
        ),
    ],
)
async def test_row_errors_have_structured_client_responses(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
    batch: bool,
    error: TableRowError,
    expected_status: int,
) -> None:
    with patch.object(tables_router, "TablesService") as service_type:
        service = AsyncMock()
        service.get_table.return_value = mock_table
        service.insert_row.side_effect = error
        service.batch_insert_rows.side_effect = error
        service_type.return_value = service
        path = f"/tables/{mock_table.id}/rows"
        payload = {"rows": [{}]} if batch else {"data": {}}
        response = client.post(
            path + ("/batch" if batch else ""),
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )
    assert response.status_code == expected_status
    assert response.json() == {"detail": error.detail}


@pytest.mark.anyio
@pytest.mark.parametrize("batch", [False, True])
async def test_unrecognized_row_database_errors_remain_server_errors(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
    batch: bool,
) -> None:
    with patch.object(tables_router, "TablesService") as service_type:
        service = AsyncMock()
        service.get_table.return_value = mock_table
        error = IntegrityError(
            "private SQL",
            {"value": "private-test-value"},
            RuntimeError("unknown failure"),
        )
        service.insert_row.side_effect = error
        service.batch_insert_rows.side_effect = error
        service_type.return_value = service
        path = f"/tables/{mock_table.id}/rows"
        response = TestClient(client.app, raise_server_exceptions=False).post(
            path + ("/batch" if batch else ""),
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"rows": [{}]} if batch else {"data": {}},
        )
    assert response.status_code == 500
    assert "private SQL" not in response.text
    assert "private-test-value" not in response.text


@pytest.mark.anyio
async def test_list_table_rows_passes_search_and_sort_params(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.list_rows.return_value = CursorPaginatedResponse(items=[])
        MockService.return_value = mock_svc

        response = client.get(
            f"/tables/{mock_table.id}/rows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "search_term": "alpha",
                "search_column": "name",
                "order_by": "name",
                "sort": "asc",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    kwargs = mock_svc.list_rows.await_args.kwargs
    assert kwargs == {
        "search_term": "alpha",
        "search_column": "name",
        "order_by": "name",
        "sort": "asc",
    }


@pytest.mark.anyio
async def test_list_table_rows_invalid_search_column_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_table.return_value = mock_table
        mock_svc.list_rows.side_effect = ValueError("Invalid search_column: missing")
        MockService.return_value = mock_svc

        response = client.get(
            f"/tables/{mock_table.id}/rows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "search_term": "alpha",
                "search_column": "missing",
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid search_column: missing"
