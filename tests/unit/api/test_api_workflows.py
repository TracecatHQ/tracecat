"""HTTP-level tests for workflow management API endpoints."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import UniqueViolationError as AsyncpgUniqueViolationError
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.types import Role
from tracecat.db.models import (
    Action,
    Schedule,
    Webhook,
    Workflow,
    WorkflowTag,
    Workspace,
)
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import CursorPaginatedResponse
from tracecat.validation.schemas import (
    ValidationDetail,
    ValidationResult,
    ValidationResultType,
)
from tracecat.workflow.management import draft
from tracecat.workflow.management import router as workflow_management_router
from tracecat.workflow.management.management import WorkflowPublishResult
from tracecat.workflow.management.types import (
    WorkflowDefinitionMinimal,
    WorkflowTriggerSummaryMinimal,
)


@pytest.fixture
def mock_workflow(test_workspace: Workspace) -> Workflow:
    """Create a mock workflow DB object."""
    workflow_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    return Workflow(
        id=workflow_id,
        title="Test Workflow",
        description="Test workflow description",
        status="online",
        version=1,
        workspace_id=test_workspace.id,
        entrypoint="action-1",
        expects={"input": {"type": "string"}},
        returns=None,
        config={},
        alias="test-workflow",
        error_handler=None,
        icon_url="https://example.com/icon.png",
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        graph_version=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        tags=[],
    )


@pytest.fixture
def mock_webhook(test_workspace: Workspace, mock_workflow: Workflow) -> Webhook:
    """Create a mock webhook DB object."""
    return Webhook(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaac"),
        workspace_id=test_workspace.id,
        workflow_id=mock_workflow.id,
        status="online",
        methods=["POST"],
        filters={},
        allowlisted_cidrs=[],
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_list_workflows_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows returns paginated list of workflows."""
    # Mock service layer
    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as MockService:
        # Create mock service instance
        mock_svc = AsyncMock()
        mock_definition = WorkflowDefinitionMinimal(
            id=str(uuid.uuid4()),
            version=1,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

        # Mock paginated response
        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, mock_definition)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response

        # Set up service mock
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/workflows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify response structure
        assert "items" in data
        assert len(data["items"]) == 1
        assert "has_more" in data
        assert "next_cursor" in data

        # Verify workflow data
        workflow = data["items"][0]
        assert workflow["title"] == "Test Workflow"
        assert workflow["description"] == "Test workflow description"
        assert workflow["status"] == "online"
        assert workflow["alias"] == "test-workflow"
        assert "latest_definition" in workflow


@pytest.mark.anyio
async def test_list_workflows_accepts_workspace_scoped_path(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workspaces/{workspace_id}/workflows resolves workspace context from the path."""
    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as MockService:
        mock_svc = AsyncMock()
        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, None)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response
        MockService.return_value = mock_svc

        response = client.get(f"/workspaces/{test_admin_role.workspace_id}/workflows")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["items"][0]["title"] == "Test Workflow"


@pytest.mark.anyio
async def test_list_workflows_with_pagination(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows with pagination parameters."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, None)],
            next_cursor="next-cursor",
            prev_cursor=None,
            has_more=True,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response
        MockService.return_value = mock_svc

        # Make request with pagination params
        response = client.get(
            "/workflows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "limit": 10,
                "cursor": "some-cursor",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["has_more"] is True
        assert data["next_cursor"] == "next-cursor"


@pytest.mark.anyio
async def test_list_workflows_with_tag_filter(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows with tag filtering."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        # Add tag to workflow
        mock_tag = WorkflowTag(
            id=uuid.uuid4(),
            name="test-tag",
            ref="test-tag",
            workspace_id=mock_workflow.workspace_id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_workflow.tags = [mock_tag]

        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, None)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response
        MockService.return_value = mock_svc

        # Make request with tag filter
        response = client.get(
            "/workflows",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "tag": "test-tag",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["tags"][0]["name"] == "test-tag"


@pytest.mark.anyio
async def test_commit_workflow_builtin_registry_not_ready_returns_validation_failure(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Commit should return a validation-style failure while builtin sync is pending."""
    # The build/validate/lock/commit orchestration lives in
    # WorkflowsManagementService.publish_workflow; the commit route just renders
    # its WorkflowPublishResult. Simulate the builtin-sync-pending failure.
    failure = WorkflowPublishResult(
        version=None,
        errors=[
            ValidationResult.new(
                type=ValidationResultType.DSL,
                status="error",
                msg="Builtin registry sync is still in progress. Please retry shortly.",
                detail=[
                    ValidationDetail(
                        type="registry.builtin_sync_pending",
                        msg="Builtin registry sync is still in progress. Please retry shortly.",
                        loc=("registry_lock",),
                    )
                ],
            )
        ],
    )

    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as mock_mgmt_cls:
        mock_mgmt = AsyncMock()
        mock_mgmt.publish_workflow.return_value = failure
        mock_mgmt_cls.return_value = mock_mgmt

        response = client.post(
            f"/workflows/{mock_workflow.id}/commit",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["status"] == "failure"
    assert payload["message"] == "1 validation error(s)"
    assert len(payload["errors"]) == 1
    error = payload["errors"][0]
    assert error["type"] == "dsl"
    assert "retry shortly" in error["msg"]
    assert error["detail"][0]["type"] == "registry.builtin_sync_pending"


@pytest.mark.anyio
async def test_create_workflow_import_builtin_registry_not_ready_returns_validation_failure(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Workflow import should return a validation-style failure while builtin sync is pending."""
    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_workflow_from_external_definition.side_effect = (
            BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            )
        )
        MockService.return_value = mock_svc

        response = client.post(
            "/workflows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            files={
                "file": (
                    "workflow.yaml",
                    b"definition:\n  title: Imported workflow\n",
                    "application/yaml",
                )
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.json()["detail"]
    assert payload["status"] == "failure"
    assert payload["message"] == "1 validation error(s)"
    error = payload["errors"][0]
    assert error["type"] == "dsl"
    assert "retry shortly" in error["msg"]
    assert error["detail"][0]["type"] == "registry.builtin_sync_pending"


@pytest.mark.anyio
async def test_list_workflows_includes_trigger_summary(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows includes trigger summary metadata when available."""
    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as MockService:
        mock_svc = AsyncMock()
        mock_definition = WorkflowDefinitionMinimal(
            id=str(uuid.uuid4()),
            version=3,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        trigger_summary = WorkflowTriggerSummaryMinimal(
            schedule_count_online=1,
            schedule_cron="0 * * * *",
            schedule_natural="Hourly at minute 00",
            webhook_active=True,
            case_trigger_events=("case_created", "status_changed"),
        )
        mock_response = CursorPaginatedResponse(
            items=[(mock_workflow, mock_definition, trigger_summary)],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_workflows.return_value = mock_response
        MockService.return_value = mock_svc

        response = client.get(
            "/workflows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["items"][0]["trigger_summary"] == {
            "schedule_count_online": 1,
            "schedule_cron": "0 * * * *",
            "schedule_natural": "Hourly at minute 00",
            "webhook_active": True,
            "case_trigger_events": ["case_created", "status_changed"],
        }


@pytest.mark.anyio
async def test_create_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test POST /workflows creates a new workflow."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.create_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/workflows",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            data={
                "title": "Test Workflow",
                "description": "Test workflow description",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()

        # Verify workflow data
        assert data["title"] == "Test Workflow"
        assert data["description"] == "Test workflow description"
        assert data["status"] == "online"
        assert "id" in data
        assert "created_at" in data


@pytest.mark.anyio
async def test_create_workflow_validation_error(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /workflows with invalid data returns 422."""
    # Make request with title that's too long (> 100 chars)
    response = client.post(
        "/workflows",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        data={
            "title": "a" * 101,
            "description": "Test description",
        },
    )

    # Should return validation error
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_get_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
    mock_webhook: Webhook,
) -> None:
    """Test GET /workflows/{id} returns workflow details."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        # Add relationships
        mock_workflow.actions = []
        mock_workflow.webhook = mock_webhook
        mock_workflow.schedules = []
        mock_svc.get_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.get(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify workflow data
        assert data["title"] == "Test Workflow"
        assert data["description"] == "Test workflow description"
        assert data["status"] == "online"
        assert data["version"] == 1
        assert "webhook" in data
        assert "actions" in data
        assert "schedules" in data


@pytest.mark.anyio
async def test_get_workflow_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workflows/{id} with non-existent ID returns 404."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = None
        MockService.return_value = mock_svc

        # Make request with non-existent ID
        fake_id = str(uuid.uuid4())
        response = client.get(
            f"/workflows/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_export_workflow_includes_layout(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    mock_workflow.trigger_position_x = 12.0
    mock_workflow.trigger_position_y = 24.0
    mock_workflow.viewport_x = 30.0
    mock_workflow.viewport_y = 40.0
    mock_workflow.viewport_zoom = 1.5
    mock_workflow.actions = [
        Action(
            id=uuid.uuid4(),
            workflow_id=mock_workflow.id,
            workspace_id=mock_workflow.workspace_id,
            type="core.transform.reshape",
            title="entrypoint_1",
            description="",
            status="offline",
            inputs="{}",
            control_flow={},
            is_interactive=False,
            interaction=None,
            position_x=100.0,
            position_y=200.0,
            upstream_edges=[],
        )
    ]

    with (
        patch(
            "tracecat.workflow.management.router.get_setting",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "tracecat.workflow.management.router.WorkflowDefinitionsService"
        ) as MockDefinitionsService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_definition_by_workflow_id.return_value = SimpleNamespace(
            workspace_id=mock_workflow.workspace_id,
            workflow_id=mock_workflow.id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            version=1,
            content={
                "title": "Test Workflow",
                "description": "Test workflow description",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    }
                ],
            },
            workflow=mock_workflow,
        )
        MockDefinitionsService.return_value = mock_svc

        response = client.get(
            f"/workflows/{mock_workflow.id}/export",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "format": "json",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    payload = json.loads(response.text)
    assert payload["layout"] == {
        "trigger": {"x": 12.0, "y": 24.0},
        "viewport": {"x": 30.0, "y": 40.0, "zoom": 1.5},
        "actions": [
            {
                "ref": "entrypoint_1",
                "x": 100.0,
                "y": 200.0,
            }
        ],
    }


@pytest.mark.anyio
async def test_update_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
    mock_webhook: Webhook,
) -> None:
    """Test PATCH /workflows/{id} updates workflow and returns it."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_workflow.actions = []
        mock_workflow.webhook = mock_webhook
        mock_workflow.schedules = []
        mock_workflow.title = "Updated Title"
        mock_svc.update_workflow.return_value = None
        mock_svc.get_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.patch(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "title": "Updated Title",
                "description": "Updated description",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == WorkflowUUID.new(mock_workflow.id).short()
        assert data["title"] == "Updated Title"

        # Verify service was called with correct params
        mock_svc.update_workflow.assert_called_once()


@pytest.mark.anyio
async def test_update_workflow_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PATCH /workflows/{id} with non-existent ID returns 404."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.update_workflow.side_effect = NoResultFound("Workflow not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.patch(
            f"/workflows/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"title": "Updated Title"},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_workflow_duplicate_alias(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PATCH /workflows/{id} with duplicate alias returns 409."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        # Create a proper IntegrityError with UniqueViolationError as cause
        unique_error = AsyncpgUniqueViolationError("uq_workflow_alias_workspace_id")
        integrity_error = IntegrityError("", {}, unique_error)
        mock_svc.update_workflow.side_effect = integrity_error
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.patch(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"alias": "duplicate-alias"},
        )

        # Should return 409 conflict
        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_restore_workflow_definition_duplicate_alias_returns_conflict(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test restore maps duplicate alias constraint violations to 409."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockManagementService,
        patch(
            "tracecat.workflow.management.router.WorkflowDefinitionsService"
        ) as MockDefinitionsService,
    ):
        definition = SimpleNamespace(
            workflow_id=mock_workflow.id,
            version=1,
        )
        unique_error = AsyncpgUniqueViolationError("uq_workflow_alias_workspace_id")
        integrity_error = IntegrityError("", {}, unique_error)
        integrity_error.__cause__ = unique_error

        mock_mgmt = AsyncMock()
        mock_mgmt.get_workflow.return_value = mock_workflow
        mock_mgmt.restore_workflow_definition.side_effect = integrity_error
        MockManagementService.return_value = mock_mgmt

        mock_definitions = AsyncMock()
        mock_definitions.get_definition_by_workflow_id.return_value = definition
        MockDefinitionsService.return_value = mock_definitions

        response = client.post(
            f"/workflows/{mock_workflow.id}/definitions/1/restore",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert (
        response.json()["detail"]
        == "Workflow alias must be unique within the workspace."
    )


@pytest.mark.anyio
async def test_restore_workflow_definition_rejects_non_positive_version(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test restore rejects invalid definition versions before service lookup."""
    response = client.post(
        f"/workflows/{mock_workflow.id}/definitions/0/restore",
        params={"workspace_id": str(test_admin_role.workspace_id)},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_delete_workflow_success(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test DELETE /workflows/{id} deletes workflow."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.delete_workflow.return_value = None
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.delete(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify service was called
        mock_svc.delete_workflow.assert_called_once()


@pytest.mark.anyio
async def test_delete_workflow_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test DELETE /workflows/{id} with non-existent ID returns 404."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.delete_workflow.side_effect = NoResultFound("Workflow not found")
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.delete(
            f"/workflows/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_workflow_with_relationships(
    client: TestClient,
    test_admin_role: Role,
    test_workspace: Workspace,
    mock_workflow: Workflow,
    mock_webhook: Webhook,
) -> None:
    """Test GET /workflows/{id} properly serializes relationships (tags, actions, schedules)."""
    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
    ):
        mock_svc = AsyncMock()

        # Add relationships to workflow
        mock_tag = WorkflowTag(
            id=uuid.uuid4(),
            name="production",
            ref="production",
            workspace_id=test_workspace.id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_action = Action(
            id=uuid.UUID("12345678-1234-4123-8123-123456789012"),
            type="webhook",
            title="Test Action",
            description="Test action description",
            status="online",
            inputs="",  # inputs is a YAML string, not dict
            control_flow={},
            is_interactive=False,
            workspace_id=test_workspace.id,
            workflow_id=mock_workflow.id,
            position_x=100.0,
            position_y=200.0,
            upstream_edges=[],
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        mock_schedule = Schedule(
            id=uuid.UUID("12345678-1234-4123-8123-123456789013"),
            status="online",
            workspace_id=test_workspace.id,
            workflow_id=mock_workflow.id,
            cron="0 0 * * *",
            inputs={},
            offset=None,
            start_at=None,
            end_at=None,
            timeout=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

        mock_workflow.tags = [mock_tag]
        mock_workflow.actions = [mock_action]
        mock_workflow.schedules = [mock_schedule]
        mock_workflow.webhook = mock_webhook

        mock_svc.get_workflow.return_value = mock_workflow
        MockService.return_value = mock_svc

        # Make request
        workflow_id = str(mock_workflow.id)
        response = client.get(
            f"/workflows/{workflow_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify relationships are properly serialized
        assert "actions" in data
        assert "12345678-1234-4123-8123-123456789012" in data["actions"]
        assert (
            data["actions"]["12345678-1234-4123-8123-123456789012"]["title"]
            == "Test Action"
        )

        assert "schedules" in data
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["cron"] == "0 0 * * *"

        assert "webhook" in data
        assert data["webhook"]["status"] == "online"


@pytest.mark.anyio
async def test_move_workflow_rejects_unknown_fields(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """POST /workflows/{id}/move with an unsupported key must not silently succeed."""
    with patch(
        "tracecat.workflow.management.router.WorkflowFolderService.move_workflow",
        new_callable=AsyncMock,
    ) as mock_move:
        response = client.post(
            f"/workflows/{mock_workflow.id}/move",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"folder_id": str(uuid.uuid4())},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_move.assert_not_awaited()


@pytest.mark.anyio
async def test_create_schedule_unpublished_workflow_returns_conflict(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    with patch(
        "tracecat.workflow.schedules.router.WorkflowSchedulesService.create_schedule",
        new_callable=AsyncMock,
        side_effect=TracecatConflictError(
            "Workflow must be saved before creating a schedule."
        ),
    ):
        response = client.post(
            "/schedules",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "workflow_id": WorkflowUUID.new(mock_workflow.id).short(),
                "cron": "0 0 * * *",
            },
        )

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_create_schedule_rejects_cron_and_every_together(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    response = client.post(
        "/schedules",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={
            "workflow_id": WorkflowUUID.new(mock_workflow.id).short(),
            "cron": "0 0 * * *",
            "every": "PT1H",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_delete_missing_schedule_returns_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch(
        "tracecat.workflow.schedules.router.WorkflowSchedulesService.delete_schedule",
        new_callable=AsyncMock,
        side_effect=TracecatNotFoundError("Schedule not found"),
    ):
        response = client.delete(
            "/schedules/sch_00000000000000000000000000000000",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def _draft_workflow(mock_workflow: Workflow) -> Workflow:
    mock_workflow.actions = []
    mock_workflow.schedules = []
    mock_workflow.entrypoint = None
    mock_workflow.expects = {}
    return mock_workflow


@pytest.mark.anyio
async def test_get_workflow_draft_returns_document_and_revision(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test GET /workflows/{id}/draft returns the editable document."""
    workflow = _draft_workflow(mock_workflow)
    with patch(
        "tracecat.workflow.management.router.WorkflowsManagementService"
    ) as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = workflow
        MockService.return_value = mock_svc

        response = client.get(
            f"/workflows/{workflow.id}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    expected = draft.build_workflow_edit_document(workflow)
    assert data["draft_revision"] == draft.compute_workflow_edit_revision(expected)
    assert data["document"]["metadata"]["title"] == "Test Workflow"
    assert data["document"]["definition"]["actions"] == []
    assert data["document"]["schedules"] == []


@pytest.mark.anyio
async def test_get_workflow_draft_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /workflows/{id}/draft with non-existent ID returns 404."""
    with patch(
        "tracecat.workflow.management.router.WorkflowsManagementService"
    ) as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = None
        MockService.return_value = mock_svc

        response = client.get(
            f"/workflows/{uuid.uuid4()}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_replace_workflow_draft_persists_changed_sections(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PUT /workflows/{id}/draft validates and persists the document."""
    workflow = _draft_workflow(mock_workflow)
    current = draft.build_workflow_edit_document(workflow)
    updated_payload = current.model_dump(mode="json")
    updated_payload["metadata"]["title"] = "Replaced title"
    updated_payload["definition"]["actions"] = [
        {"ref": "notify", "action": "core.transform.reshape", "args": {"value": 1}}
    ]
    updated_payload["definition"]["entrypoint"]["ref"] = "notify"
    updated_payload["layout"]["actions"] = [{"ref": "notify", "x": 10.0, "y": 20.0}]

    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
        patch(
            "tracecat.workflow.management.router.validate_workflow_edit_document",
            new_callable=AsyncMock,
        ) as mock_validate,
        patch(
            "tracecat.workflow.management.router.persist_workflow_edit_document",
            new_callable=AsyncMock,
        ) as mock_persist,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = workflow
        MockService.return_value = mock_svc

        response = client.put(
            f"/workflows/{workflow.id}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "document": updated_payload,
                "base_revision": draft.compute_workflow_edit_revision(current),
            },
        )

    assert response.status_code == status.HTTP_200_OK
    mock_svc.get_workflow.assert_awaited_once()
    assert mock_svc.get_workflow.await_args is not None
    assert mock_svc.get_workflow.await_args.kwargs == {"for_update": True}

    mock_validate.assert_awaited_once()
    assert mock_validate.await_args is not None
    validate_kwargs = mock_validate.await_args.kwargs
    assert validate_kwargs["validate_definition"] is True
    assert validate_kwargs["changed_sections"] == {"metadata", "definition", "layout"}

    mock_persist.assert_awaited_once()
    assert mock_persist.await_args is not None
    persist_kwargs = mock_persist.await_args.kwargs
    assert persist_kwargs["workflow"] is workflow
    assert persist_kwargs["original_document"] == current
    assert persist_kwargs["updated_document"].metadata.title == "Replaced title"
    assert persist_kwargs["changed_sections"] == {"metadata", "definition", "layout"}
    assert response.json()["draft_revision"]


@pytest.mark.anyio
async def test_replace_workflow_draft_omitted_schedules_left_untouched(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PUT /workflows/{id}/draft without ``schedules`` keeps existing ones."""
    workflow = _draft_workflow(mock_workflow)
    workflow.schedules = [
        Schedule(
            id=uuid.UUID("12345678-1234-4123-8123-123456789013"),
            status="online",
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            cron="0 0 * * *",
            inputs={},
            offset=None,
            start_at=None,
            end_at=None,
            timeout=None,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    ]
    current = draft.build_workflow_edit_document(workflow)
    assert len(current.schedules) == 1
    updated_payload = current.model_dump(mode="json")
    updated_payload["metadata"]["title"] = "Replaced title"
    del updated_payload["schedules"]

    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
        patch(
            "tracecat.workflow.management.router.validate_workflow_edit_document",
            new_callable=AsyncMock,
        ),
        patch(
            "tracecat.workflow.management.router.persist_workflow_edit_document",
            new_callable=AsyncMock,
        ) as mock_persist,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = workflow
        MockService.return_value = mock_svc

        response = client.put(
            f"/workflows/{workflow.id}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"document": updated_payload},
        )

    assert response.status_code == status.HTTP_200_OK
    mock_persist.assert_awaited_once()
    assert mock_persist.await_args is not None
    persist_kwargs = mock_persist.await_args.kwargs
    assert persist_kwargs["changed_sections"] == {"metadata"}
    assert persist_kwargs["updated_document"].schedules == current.schedules


@pytest.mark.anyio
async def test_replace_workflow_draft_stale_base_revision_returns_conflict(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PUT /workflows/{id}/draft returns 409 on base_revision mismatch."""
    workflow = _draft_workflow(mock_workflow)
    current = draft.build_workflow_edit_document(workflow)

    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
        patch(
            "tracecat.workflow.management.router.persist_workflow_edit_document",
            new_callable=AsyncMock,
        ) as mock_persist,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = workflow
        MockService.return_value = mock_svc

        response = client.put(
            f"/workflows/{workflow.id}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "document": current.model_dump(mode="json"),
                "base_revision": "stale",
            },
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    detail = response.json()["detail"]
    assert detail["type"] == "conflict"
    assert detail["current_revision"] == draft.compute_workflow_edit_revision(current)
    mock_persist.assert_not_awaited()


@pytest.mark.anyio
async def test_replace_workflow_draft_validation_error_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PUT /workflows/{id}/draft surfaces DSL validation errors as 400."""
    workflow = _draft_workflow(mock_workflow)
    current = draft.build_workflow_edit_document(workflow)
    details = {
        "type": "validation_error",
        "message": "1 validation error(s)",
        "status": "error",
        "errors": [{"type": "dsl", "message": "bad action"}],
    }

    with (
        patch(
            "tracecat.workflow.management.router.WorkflowsManagementService"
        ) as MockService,
        patch(
            "tracecat.workflow.management.router.validate_workflow_edit_document",
            new_callable=AsyncMock,
            side_effect=draft.WorkflowEditError(
                "1 validation error(s)", code="validation_error", details=details
            ),
        ),
        patch(
            "tracecat.workflow.management.router.persist_workflow_edit_document",
            new_callable=AsyncMock,
        ) as mock_persist,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = workflow
        MockService.return_value = mock_svc

        response = client.put(
            f"/workflows/{workflow.id}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"document": current.model_dump(mode="json")},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == details
    mock_persist.assert_not_awaited()


@pytest.mark.anyio
async def test_replace_workflow_draft_rejects_unknown_fields(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    """Test PUT /workflows/{id}/draft rejects unknown document keys with 422."""
    workflow = _draft_workflow(mock_workflow)
    current = draft.build_workflow_edit_document(workflow)
    payload = current.model_dump(mode="json")
    payload["metadata"]["folder_id"] = str(uuid.uuid4())

    with patch(
        "tracecat.workflow.management.router.WorkflowsManagementService"
    ) as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = workflow
        MockService.return_value = mock_svc

        response = client.put(
            f"/workflows/{workflow.id}/draft",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"document": payload},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
