"""HTTP-level tests for executor/internal endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.pagination import CursorPaginatedResponse
from tracecat.tables import router as tables_router


@pytest.mark.anyio
async def test_executor_tables_by_name_is_routed(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(tables_router, "get_table_by_name", new=AsyncMock()) as mocked:
        mocked.return_value = {
            "id": "ffffffff-ffff-4fff-ffff-ffffffffffff",
            "name": "test_table",
            "columns": [],
        }
        response = client.get(
            "/internal/tables/by-name/test_table",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "test_table"


@pytest.mark.anyio
async def test_executor_cases_list_is_routed(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    from tracecat.cases import router as cases_router

    with patch.object(cases_router, "list_cases", new=AsyncMock()) as mocked:
        mocked.return_value = CursorPaginatedResponse(
            items=[],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        response = client.get(
            "/internal/cases",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["items"] == []
