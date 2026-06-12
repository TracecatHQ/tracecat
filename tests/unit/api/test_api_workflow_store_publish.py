"""HTTP-level tests for workflow publish API endpoint."""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session, get_async_session_bypass_rls
from tracecat.exceptions import TracecatValidationError
from tracecat.registry.repositories.schemas import GitBranchInfo
from tracecat.vcs.github.app import GitHubAppError
from tracecat.workflow.store.schemas import WorkflowDslPublishResult


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
async def test_publish_workflow_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /workflows/{workflow_id}/publish returns structured publish result."""
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
        mock_store_svc.publish_workflow_dsl.return_value = WorkflowDslPublishResult(
            status="committed",
            commit_sha="abc123",
            branch="feature/shared-workflow",
            base_branch="main",
            pr_url="https://github.com/test-org/test-repo/pull/123",
            pr_number=123,
            pr_reused=False,
            message="Committed workflow changes.",
        )
        mock_store_cls.return_value = mock_store_svc

        response = client.post(
            f"/workflows/{workflow_id}/publish",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "message": "Update workflow",
                "branch": "feature/shared-workflow",
                "create_pr": True,
            },
        )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["status"] == "committed"
    assert payload["commit_sha"] == "abc123"
    assert payload["branch"] == "feature/shared-workflow"
    assert payload["base_branch"] == "main"
    assert payload["pr_url"] == "https://github.com/test-org/test-repo/pull/123"
    assert payload["pr_number"] == 123
    assert payload["pr_reused"] is False

    called_params = mock_store_svc.publish_workflow_dsl.call_args.kwargs["params"]
    assert called_params.branch == "feature/shared-workflow"
    assert called_params.create_pr is True


@pytest.mark.anyio
async def test_publish_workflow_invalid_branch_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test branch validation errors from service return 400."""
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
        mock_store_svc.publish_workflow_dsl.side_effect = TracecatValidationError(
            "branch must be a short branch name, not a full ref (refs/...)"
        )
        mock_store_cls.return_value = mock_store_svc

        response = client.post(
            f"/workflows/{workflow_id}/publish",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "branch": "refs/heads/feature/shared-workflow",
                "create_pr": False,
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "short branch name" in response.json()["detail"]


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
async def test_list_workflow_branches_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workflows/sync/branches returns branch list."""
    with (
        patch("tracecat.workflow.store.router.WorkspaceService") as mock_workspace_cls,
        patch("tracecat.workflow.store.router.WorkflowSyncService") as mock_sync_cls,
    ):
        mock_workspace_svc = AsyncMock()
        mock_workspace = Mock()
        mock_workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/test-org/test-repo.git"
        }
        mock_workspace_svc.get_workspace.return_value = mock_workspace
        mock_workspace_cls.return_value = mock_workspace_svc

        mock_sync_svc = AsyncMock()
        mock_sync_svc.list_branches.return_value = [
            GitBranchInfo(name="main", is_default=True),
            GitBranchInfo(name="feature/workflow-publish", is_default=False),
        ]
        mock_sync_cls.return_value = mock_sync_svc

        response = client.get(
            "/workflows/sync/branches",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload == [
        {"name": "main", "is_default": True},
        {"name": "feature/workflow-publish", "is_default": False},
    ]


@pytest.mark.anyio
async def test_list_workflow_branches_missing_repo_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workflows/sync/branches returns 400 when repo URL is missing."""
    with patch("tracecat.workflow.store.router.WorkspaceService") as mock_workspace_cls:
        mock_workspace_svc = AsyncMock()
        mock_workspace = Mock()
        mock_workspace.settings = {}
        mock_workspace_svc.get_workspace.return_value = mock_workspace
        mock_workspace_cls.return_value = mock_workspace_svc

        response = client.get(
            "/workflows/sync/branches",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Git repository URL not configured" in response.json()["detail"]


@pytest.mark.anyio
async def test_pull_workflows_blocked_when_org_flag_set(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """POST /workflows/sync/pull returns 403 when the org has pulls disabled."""
    org_result = Mock()
    org_result.scalar_one_or_none.return_value = True

    session_mock = AsyncMock(name="org_disabled_session")
    session_mock.execute = AsyncMock(return_value=org_result)

    async def _override_session() -> AsyncMock:
        return session_mock

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_async_session_bypass_rls] = _override_session

    response = client.post(
        "/workflows/sync/pull",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={"commit_sha": "abc1234567890", "dry_run": False},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "disabled by your organization administrator" in response.json()["detail"]


@pytest.mark.anyio
async def test_pull_workflows_passes_gate_when_org_flag_unset(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """POST /workflows/sync/pull proceeds past the gate when the flag is off."""
    org_result = Mock()
    org_result.scalar_one_or_none.return_value = False

    session_mock = AsyncMock(name="org_allowed_session")
    session_mock.execute = AsyncMock(return_value=org_result)

    async def _override_session() -> AsyncMock:
        return session_mock

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_async_session_bypass_rls] = _override_session

    with (
        patch("tracecat.workflow.store.router.WorkspaceService") as mock_workspace_cls,
        patch("tracecat.workflow.store.router.WorkflowSyncService") as mock_sync_cls,
    ):
        mock_workspace_svc = AsyncMock()
        mock_workspace = Mock()
        mock_workspace.settings = {"git_repo_url": ""}
        mock_workspace_svc.get_workspace.return_value = mock_workspace
        mock_workspace_cls.return_value = mock_workspace_svc

        mock_sync_cls.return_value = AsyncMock()

        response = client.post(
            "/workflows/sync/pull",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"commit_sha": "abc1234567890", "dry_run": False},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Git repository URL not configured" in response.json()["detail"]


@pytest.mark.anyio
async def test_list_workflow_branches_github_error_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workflows/sync/branches maps GitHub errors to 400."""
    with (
        patch("tracecat.workflow.store.router.WorkspaceService") as mock_workspace_cls,
        patch("tracecat.workflow.store.router.WorkflowSyncService") as mock_sync_cls,
    ):
        mock_workspace_svc = AsyncMock()
        mock_workspace = Mock()
        mock_workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/test-org/test-repo.git"
        }
        mock_workspace_svc.get_workspace.return_value = mock_workspace
        mock_workspace_cls.return_value = mock_workspace_svc

        mock_sync_svc = AsyncMock()
        mock_sync_svc.list_branches.side_effect = GitHubAppError(
            "Unable to access repository"
        )
        mock_sync_cls.return_value = mock_sync_svc

        response = client.get(
            "/workflows/sync/branches",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unable to access repository" in response.json()["detail"]
