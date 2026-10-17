"""HTTP-level tests for tables API endpoints."""

import uuid
from datetime import UTC, datetime
from types import ModuleType
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
from tracecat.tables import internal_router as internal_tables_router
from tracecat.tables import router as tables_router
from tracecat.tables.enums import SqlType
from tracecat.tables.exceptions import TableRowError


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


def _duplicate_table_error() -> ProgrammingError:
    duplicate_error = DuplicateTableError("Table already exists")
    programming_error = ProgrammingError("", {}, duplicate_error)
    programming_error.__cause__ = duplicate_error
    return programming_error


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code"),
    [
        pytest.param(
            "POST",
            "/tables",
            {
                "name": "duplicate_table",
                "description": "Duplicate table",
                "columns": [{"name": "id", "type": SqlType.TEXT.value}],
            },
            "create_table",
            _duplicate_table_error(),
            status.HTTP_409_CONFLICT,
            id="create-duplicate",
        ),
        pytest.param(
            "GET",
            f"/tables/{uuid.uuid4()}",
            None,
            "get_table",
            TracecatNotFoundError("Table not found"),
            status.HTTP_404_NOT_FOUND,
            id="get-not-found",
        ),
    ],
)
async def test_table_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    service_method: str,
    error: Exception,
    status_code: int,
) -> None:
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = error
        MockService.return_value = mock_svc

        response = client.request(
            method,
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status_code


_TABLE_SURFACE_PARAMS = [
    pytest.param(
        "client",
        tables_router,
        "/tables/ffffffff-ffff-4fff-ffff-ffffffffffff",
        "get_table",
        id="public",
    ),
    pytest.param(
        "action_gateway_client",
        internal_tables_router,
        "/internal/tables/test_table",
        "get_table_by_name",
        id="internal",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_fixture", "router", "base_path", "get_table_method"),
    _TABLE_SURFACE_PARAMS,
)
@pytest.mark.parametrize(
    ("method", "suffix", "service_method", "message", "payload"),
    [
        pytest.param(
            "POST",
            "/rows",
            "insert_row",
            "Invalid numeric value: 'abc'",
            {"data": {"score": "abc"}},
            id="insert-invalid-numeric",
        ),
        pytest.param(
            "PATCH",
            f"/rows/{uuid.uuid4()}",
            "update_row",
            "Invalid integer value: '1.5'",
            {"data": {"attempts": "1.5"}},
            id="update-invalid-integer",
        ),
    ],
)
async def test_invalid_row_values_return_400(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    mock_table: Table,
    client_fixture: str,
    router: ModuleType,
    base_path: str,
    get_table_method: str,
    method: str,
    suffix: str,
    service_method: str,
    message: str,
    payload: dict[str, object],
) -> None:
    """Invalid row input should be surfaced as a client error."""
    http: TestClient = request.getfixturevalue(client_fixture)
    with patch.object(router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, get_table_method).return_value = mock_table
        getattr(mock_svc, service_method).side_effect = ValueError(message)
        MockService.return_value = mock_svc

        response = http.request(
            method,
            base_path + suffix,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_fixture", "router", "base_path", "get_table_method"),
    _TABLE_SURFACE_PARAMS,
)
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
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    mock_table: Table,
    client_fixture: str,
    router: ModuleType,
    base_path: str,
    get_table_method: str,
    batch: bool,
    error: TableRowError,
    expected_status: int,
) -> None:
    http: TestClient = request.getfixturevalue(client_fixture)
    with patch.object(router, "TablesService") as service_type:
        service = AsyncMock()
        getattr(service, get_table_method).return_value = mock_table
        service.insert_row.side_effect = error
        service.batch_insert_rows.side_effect = error
        service_type.return_value = service
        payload = {"rows": [{}]} if batch else {"data": {}}
        response = http.post(
            f"{base_path}/rows" + ("/batch" if batch else ""),
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )
    assert response.status_code == expected_status
    assert response.json() == {"detail": error.detail}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_fixture", "router", "base_path", "get_table_method"),
    _TABLE_SURFACE_PARAMS,
)
@pytest.mark.parametrize("batch", [False, True])
async def test_unrecognized_row_database_errors_remain_server_errors(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    mock_table: Table,
    client_fixture: str,
    router: ModuleType,
    base_path: str,
    get_table_method: str,
    batch: bool,
) -> None:
    http: TestClient = request.getfixturevalue(client_fixture)
    with patch.object(router, "TablesService") as service_type:
        service = AsyncMock()
        getattr(service, get_table_method).return_value = mock_table
        error = IntegrityError(
            "private SQL",
            {"value": "private-test-value"},
            RuntimeError("unknown failure"),
        )
        service.insert_row.side_effect = error
        service.batch_insert_rows.side_effect = error
        service_type.return_value = service
        response = TestClient(http.app, raise_server_exceptions=False).post(
            f"{base_path}/rows" + ("/batch" if batch else ""),
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


@pytest.mark.anyio
async def test_list_table_rows_overlong_search_term_returns_422(
    client: TestClient,
    test_admin_role: Role,
    mock_table: Table,
) -> None:
    with patch.object(tables_router, "TablesService") as MockService:
        mock_svc = AsyncMock()
        MockService.return_value = mock_svc

        response = client.get(
            f"/tables/{mock_table.id}/rows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "search_term": "a" * 1001,
                "search_column": "name",
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_svc.list_rows.assert_not_awaited()
