"""HTTP-level tests for workflow execution search endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.workflow.executions import router as executions_router
from tracecat.workflow.executions.enums import (
    WORKFLOW_RUN_EXCLUDED_WORKFLOW_TYPES,
    ExecutionType,
)
from tracecat.workflow.executions.service import WorkflowExecutionsPage


def _empty_page() -> WorkflowExecutionsPage:
    return WorkflowExecutionsPage(
        items=[],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )


@pytest.mark.anyio
async def test_search_workflow_executions_accepts_limit_500(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    mock_service = AsyncMock()
    mock_service.list_executions_paginated = AsyncMock(return_value=_empty_page())

    with patch.object(
        executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_service),
    ):
        response = client.get("/workflow-executions/search", params={"limit": 500})

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["items"] == []
    assert payload["has_more"] is False
    assert payload["has_previous"] is False

    await_args = mock_service.list_executions_paginated.await_args
    assert await_args is not None
    pagination = await_args.kwargs["pagination"]
    assert pagination.limit == 500
    assert await_args.kwargs["execution_types"] == {ExecutionType.PUBLISHED}
    assert await_args.kwargs["exclude_workflow_types"] == set(
        WORKFLOW_RUN_EXCLUDED_WORKFLOW_TYPES
    )


@pytest.mark.anyio
async def test_list_workflow_executions_excludes_agent_workflow_types(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    mock_service = AsyncMock()
    mock_service.list_executions = AsyncMock(return_value=[])

    with patch.object(
        executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_service),
    ):
        response = client.get("/workflow-executions")

    assert response.status_code == status.HTTP_200_OK

    await_args = mock_service.list_executions.await_args
    assert await_args is not None
    assert await_args.kwargs["exclude_workflow_types"] == set(
        WORKFLOW_RUN_EXCLUDED_WORKFLOW_TYPES
    )


@pytest.mark.anyio
async def test_search_workflow_executions_rejects_limit_above_max(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    response = client.get("/workflow-executions/search", params={"limit": 1001})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("params", "detail"),
    [
        (
            {
                "start_time_from": "2026-01-02T00:00:00Z",
                "start_time_to": "2026-01-01T00:00:00Z",
            },
            "start_time_from must be before start_time_to",
        ),
        (
            {
                "close_time_from": "2026-01-02T00:00:00Z",
                "close_time_to": "2026-01-01T00:00:00Z",
            },
            "close_time_from must be before close_time_to",
        ),
        (
            {
                "duration_gte_seconds": 10,
                "duration_lte_seconds": 1,
            },
            "duration_gte_seconds must be <= duration_lte_seconds",
        ),
    ],
)
async def test_search_workflow_executions_returns_400_for_invalid_ranges(
    client: TestClient,
    test_admin_role: Role,
    params: dict[str, str | int],
    detail: str,
) -> None:
    connect_mock = AsyncMock()

    with patch.object(
        executions_router.WorkflowExecutionsService,
        "connect",
        connect_mock,
    ):
        response = client.get("/workflow-executions/search", params=params)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == detail
    connect_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_search_workflow_executions_maps_service_value_error_to_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    expected_detail = "Cursor no longer matches current filters. Retry without cursor."
    mock_service = AsyncMock()
    mock_service.list_executions_paginated = AsyncMock(
        side_effect=ValueError(expected_detail)
    )

    with patch.object(
        executions_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=mock_service),
    ):
        response = client.get("/workflow-executions/search")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == expected_detail


@pytest.mark.anyio
async def test_search_workflow_executions_short_circuits_when_search_has_no_matches(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    resolve_mock = AsyncMock(return_value=[])
    connect_mock = AsyncMock()

    with (
        patch.object(
            executions_router,
            "_resolve_workflow_ids_by_search_term",
            resolve_mock,
        ),
        patch.object(
            executions_router.WorkflowExecutionsService,
            "connect",
            connect_mock,
        ),
    ):
        response = client.get(
            "/workflow-executions/search",
            params={"search_term": "test query"},
        )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["items"] == []
    assert payload["next_cursor"] is None
    assert payload["has_more"] is False
    assert payload["has_previous"] is False
    resolve_mock.assert_awaited_once()
    connect_mock.assert_not_awaited()
