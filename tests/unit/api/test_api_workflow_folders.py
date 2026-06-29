"""HTTP-level tests for workflow folder API endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.workflow.management.folders import router as workflow_folder_router
from tracecat.workflow.management.folders.service import WorkflowFolderErrorCode


def _mock_service_with_async_method(
    method_name: str,
    *,
    side_effect: Exception | None = None,
    return_value: object = None,
) -> MagicMock:
    service = MagicMock()
    service_method = AsyncMock()
    if side_effect is not None:
        service_method.side_effect = side_effect
    else:
        service_method.return_value = return_value
    setattr(service, method_name, service_method)
    return service


@pytest.mark.anyio
async def test_update_folder_conflict_returns_409(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Duplicate workflow folder rename paths should surface as conflicts."""
    with patch.object(
        workflow_folder_router, "WorkflowFolderService"
    ) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "rename_folder",
            side_effect=TracecatValidationError(
                "Duplicate target",
                detail={"code": WorkflowFolderErrorCode.CONFLICT.value},
            ),
        )
        mock_service_cls.return_value = mock_service

        folder_id = uuid.uuid4()
        response = client.patch(f"/folders/{folder_id}", json={"name": "detections"})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Duplicate target"
    mock_service.rename_folder.assert_awaited_once_with(folder_id, "detections")
