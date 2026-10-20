"""HTTP-level tests for workflow publish API endpoint."""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatSettingsError, TracecatValidationError
from tracecat.vcs.github.app import GitHubAppError
from tracecat.vcs.gitlab.app import GitLabError


def _sample_dsl_content() -> dict[str, object]:
    return {
        "title": "Test workflow",
        "description": "A test workflow",
        "entrypoint": {"ref": "start", "expects": {}},
        "actions": [
            {
                "ref": "start",
                "action": "core.transform.passthrough",
                "args": {"value": "test"},
            }
        ],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "payload", "detail_fragment"),
    [
        pytest.param(
            TracecatValidationError(
                "branch must be a short branch name, not a full ref (refs/...)"
            ),
            {"branch": "refs/heads/feature/shared-workflow", "create_pr": False},
            "short branch name",
            id="invalid-branch",
        ),
        pytest.param(
            GitLabError(
                "Failed to retrieve GitLab token credentials: credentials not found"
            ),
            {"branch": "feature/shared-workflow", "create_pr": True},
            "GitLab token credentials",
            id="vcs-provider-error",
        ),
    ],
)
async def test_publish_workflow_errors_return_400(
    client: TestClient,
    test_admin_role: Role,
    error: Exception,
    payload: dict[str, object],
    detail_fragment: str,
) -> None:
    """Publish maps branch validation and VCS provider errors to 400."""
    workflow_id = str(uuid.uuid4())
    workflow = Mock()
    workflow.id = uuid.UUID(workflow_id)

    definition = Mock()
    definition.content = _sample_dsl_content()
    definition.workflow = workflow

    with (
        patch(
            "tracecat.workflow.store.router.WorkflowDefinitionsService"
        ) as mock_defn_cls,
        patch("tracecat.workflow.store.router.WorkflowStoreService") as mock_store_cls,
    ):
        mock_defn_svc = AsyncMock()
        mock_defn_svc.get_definition_by_workflow_id.return_value = definition
        mock_defn_cls.return_value = mock_defn_svc

        mock_store_svc = AsyncMock()
        mock_store_svc.publish_workflow_dsl.side_effect = error
        mock_store_cls.return_value = mock_store_svc

        response = client.post(
            f"/workflows/{workflow_id}/publish",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert detail_fragment in response.json()["detail"]


@pytest.mark.anyio
async def test_publish_workflow_definition_not_found_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test publish returns 404 when workflow definition does not exist."""
    workflow_id = str(uuid.uuid4())

    with patch(
        "tracecat.workflow.store.router.WorkflowDefinitionsService"
    ) as mock_defn_cls:
        mock_defn_svc = AsyncMock()
        mock_defn_svc.get_definition_by_workflow_id.return_value = None
        mock_defn_cls.return_value = mock_defn_svc

        response = client.post(
            f"/workflows/{workflow_id}/publish",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"branch": "feature/shared-workflow", "create_pr": False},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Workflow definition not found"


@pytest.mark.anyio
async def test_publish_workflow_invalid_create_pr_type_returns_422(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test request validation rejects invalid create_pr type."""
    workflow_id = str(uuid.uuid4())
    response = client.post(
        f"/workflows/{workflow_id}/publish",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={
            "branch": "feature/shared-workflow",
            "create_pr": {"invalid": True},
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "detail_fragment"),
    [
        pytest.param(
            TracecatSettingsError(
                "Git repository URL not configured for this workspace."
            ),
            "Git repository URL not configured",
            id="missing-repo",
        ),
        pytest.param(
            GitHubAppError("Unable to access repository"),
            "Unable to access repository",
            id="github-error",
        ),
    ],
)
async def test_list_workflow_branches_errors_return_400(
    client: TestClient,
    test_admin_role: Role,
    error: Exception,
    detail_fragment: str,
) -> None:
    """GET /workflows/sync/branches maps settings and provider errors to 400."""
    with patch("tracecat.workflow.store.router.WorkspaceSyncService") as mock_sync_cls:
        mock_sync_svc = AsyncMock()
        mock_sync_svc.list_branches.side_effect = error
        mock_sync_cls.for_workspace = AsyncMock(return_value=mock_sync_svc)

        response = client.get(
            "/workflows/sync/branches",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert detail_fragment in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/workflows/sync/export",
            {"message": "Sync workspace", "branch": "sync/workspace"},
        ),
        ("/workflows/sync/export/preview", {}),
    ],
)
@pytest.mark.anyio
async def test_workspace_sync_export_routes_map_service_construction_errors_to_400(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    payload: dict[str, object],
) -> None:
    with patch("tracecat.workflow.store.router.WorkspaceSyncService") as mock_sync_cls:
        mock_sync_cls.for_workspace = AsyncMock(
            side_effect=TracecatSettingsError(
                "Unsupported Git provider configured for this workspace: unknown"
            )
        )

        response = client.post(
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unsupported Git provider configured" in response.json()["detail"]
