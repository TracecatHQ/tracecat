"""HTTP-level tests for workflow actions API endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.workflow.actions.service import WorkflowActionService

_WORKFLOW_ID = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
_MISSING_ACTION_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(autouse=True)
def no_managed_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use mocked sessions, which cannot answer managed lookups."""
    monkeypatch.setattr(
        "tracecat.managed_resources.guard.is_managed",
        AsyncMock(return_value=False),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code"),
    [
        pytest.param(
            "POST",
            "/actions",
            {
                "workflow_id": _WORKFLOW_ID,
                "type": "core.http_request",
                "title": "Test Action",
                "inputs": "url: https://example.com\nmethod: GET",
            },
            "create_action",
            TracecatValidationError("ref exists"),
            status.HTTP_409_CONFLICT,
            id="create-conflict",
        ),
        pytest.param(
            "GET",
            f"/actions/{_MISSING_ACTION_ID}",
            None,
            "get_action",
            None,
            status.HTTP_404_NOT_FOUND,
            id="get-not-found",
        ),
        pytest.param(
            "POST",
            f"/actions/{_MISSING_ACTION_ID}",
            {"title": "Updated Title"},
            "get_action",
            None,
            status.HTTP_404_NOT_FOUND,
            id="update-not-found",
        ),
    ],
)
async def test_action_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, str] | None,
    service_method: str,
    error: Exception | None,
    status_code: int,
) -> None:
    with patch.object(
        WorkflowActionService, service_method, new_callable=AsyncMock
    ) as mock_method:
        if error is None:
            mock_method.return_value = None
        else:
            mock_method.side_effect = error

        response = client.request(
            method,
            path,
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "workflow_id": _WORKFLOW_ID,
            },
            json=payload,
        )

    assert response.status_code == status_code
