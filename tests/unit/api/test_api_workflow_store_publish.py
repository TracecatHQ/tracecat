"""HTTP-level tests for workflow publish API endpoint."""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.db.models import Workflow
from tracecat.exceptions import TracecatSettingsError, TracecatValidationError
from tracecat.vcs.github.app import GitHubAppError
from tracecat.vcs.gitlab.app import GitLabError
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.exceptions import WorkflowExportValidationError
from tracecat.workspace_sync.schemas import WorkflowExportDiagnostic
from tracecat.workspace_sync.types import WorkflowExportCode, WorkflowExportStage


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
        patch(
            "tracecat.workflow.store.router.WorkflowsManagementService"
        ) as mock_mgmt_cls,
    ):
        mock_defn_svc = AsyncMock()
        mock_defn_svc.get_definition_by_workflow_id.return_value = definition
        mock_mgmt_cls.return_value.get_workflow = AsyncMock(return_value=workflow)
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
async def test_publish_workflow_not_found_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """A missing workflow remains a 404, independently of saved definitions."""
    workflow_id = str(uuid.uuid4())
    with patch("tracecat.workflow.store.router.WorkflowsManagementService") as mgmt:
        mgmt.return_value.get_workflow = AsyncMock(return_value=None)
        response = client.post(
            f"/workflows/{workflow_id}/publish",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"branch": "feature/shared-workflow", "create_pr": False},
        )
        mgmt.return_value.get_workflow.assert_awaited_once_with(
            uuid.UUID(workflow_id),
            normalize=False,
        )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Workflow not found"


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/workflows/sync/export", "/workflows/sync/export/preview"]
)
async def test_export_validation_response_preserves_legacy_detail_and_typed_context(
    client: TestClient,
    test_admin_role: Role,
    path: str,
) -> None:
    diagnostic = WorkflowExportDiagnostic(
        workflow_id=uuid.uuid4(),
        workflow_title="Example review",
        stage="definition",
        code="no_actions",
        message="Add an action before pushing this workflow.",
    )
    error = WorkflowExportValidationError([diagnostic])
    with patch("tracecat.workflow.store.router.WorkspaceSyncService") as sync:
        service = AsyncMock()
        service.export_workspace.side_effect = error
        service.preview_export_workspace.side_effect = error
        sync.for_workspace = AsyncMock(return_value=service)
        response = client.post(
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"message": "Synthetic export", "branch": "sync/example"},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == str(error)
    assert response.json()["diagnostics"] == [diagnostic.model_dump(mode="json")]
    assert response.json()["omitted_count"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("version", "content", "code"),
    [
        (None, None, "publish_required"),
        (2, None, "missing_definition"),
        (1, {}, "invalid_definition"),
        (None, {}, "invalid_definition"),
    ],
)
async def test_workflow_push_distinguishes_first_publish_from_broken_definitions(
    client: TestClient,
    test_admin_role: Role,
    version: int | None,
    content: dict[str, object] | None,
    code: str,
) -> None:
    workflow = Mock()
    workflow.id = uuid.uuid4()
    workflow.title = "Synthetic workflow"
    workflow.version = version

    async def diagnostic(
        workflow: Workflow,
        *,
        code: WorkflowExportCode,
        message: str,
        stage: WorkflowExportStage,
        provider: VcsProvider | None = None,
    ) -> WorkflowExportDiagnostic:
        return WorkflowExportDiagnostic(
            workflow_id=workflow.id,
            workflow_title=workflow.title,
            code=code,
            stage=stage,
            message=message,
        )

    with (
        patch(
            "tracecat.workflow.store.router.WorkflowsManagementService"
        ) as management,
        patch(
            "tracecat.workflow.store.router.WorkflowDefinitionsService"
        ) as definitions,
        patch("tracecat.workflow.store.router.WorkflowStoreService") as store,
        patch(
            "tracecat.workspace_sync.export_diagnostics.WorkflowExportDiagnosticsService.diagnostic",
            new=AsyncMock(side_effect=diagnostic),
        ),
    ):
        management.return_value.get_workflow = AsyncMock(return_value=workflow)
        definition = Mock(content=content) if content is not None else None
        definitions.return_value.get_definition_by_workflow_id = AsyncMock(
            return_value=definition
        )
        response = client.post(
            f"/workflows/{workflow.id}/publish",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"branch": "sync/example", "create_pr": False},
        )
        store.return_value.publish_workflow_dsl.assert_not_called()
        management.return_value.build_dsl_from_workflow.assert_not_called()
        definitions.return_value.create_workflow_definition.assert_not_called()
    assert response.status_code == 400
    body = response.json()
    assert isinstance(body["detail"], str)
    assert body["diagnostics"][0]["code"] == code
    assert body["diagnostics"][0]["workflow_id"] == str(workflow.id)
    if code == "publish_required":
        assert (
            body["diagnostics"][0]["message"]
            == "Publish this workflow before pushing it to Git."
        )
