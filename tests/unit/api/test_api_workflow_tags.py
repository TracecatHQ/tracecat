"""HTTP-level tests for workflow tags API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound

from tracecat.auth.types import Role
from tracecat.workflow.tags import router as workflow_tags_router


@pytest.mark.anyio
async def test_add_tag_conflict_on_duplicate_assignment(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """POST /workflows/{workflow_id}/tags returns 409 on duplicate assignment."""
    workflow_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    tag_id = uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")

    with patch.object(workflow_tags_router, "WorkflowTagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.add_workflow_tag.side_effect = ValueError(
            "Tag already assigned to workflow"
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/workflows/{workflow_id}/tags",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"tag_id": str(tag_id)},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Tag already assigned to workflow"


@pytest.mark.anyio
async def test_add_tag_not_found_when_workflow_or_tag_missing(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """POST /workflows/{workflow_id}/tags returns 404 when resources are missing."""
    workflow_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    tag_id = uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")

    with patch.object(workflow_tags_router, "WorkflowTagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.add_workflow_tag.side_effect = NoResultFound(
            "Workflow or tag not found"
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/workflows/{workflow_id}/tags",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"tag_id": str(tag_id)},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Workflow or tag not found"
