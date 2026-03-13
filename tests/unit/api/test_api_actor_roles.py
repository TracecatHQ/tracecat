"""HTTP-level tests for actor-aware user and service-account routes."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.dependencies import WorkspaceActorRole, WorkspaceUserRole
from tracecat.auth.types import Role
from tracecat.cases import router as cases_router
from tracecat.contexts import ctx_role
from tracecat.db.models import Webhook, Workflow, Workspace
from tracecat.integrations import router as integrations_router
from tracecat.integrations.enums import OAuthGrantType
from tracecat.pagination import CursorPaginatedResponse
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management import router as workflow_management_router
from tracecat.workflow.management.types import WorkflowDefinitionMinimal
from tracecat.workspaces import router as workspaces_router


@pytest.fixture
def workspace_targeted_service_account_role(test_admin_role: Role) -> Role:
    workspace_id = test_admin_role.workspace_id
    organization_id = test_admin_role.organization_id
    assert workspace_id is not None
    assert organization_id is not None
    return Role(
        type="service_account",
        service_id="tracecat-api",
        organization_id=organization_id,
        workspace_id=workspace_id,
        bound_workspace_id=workspace_id,
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"workflow:read", "workspace:read"}),
    )


@pytest.fixture
def workspace_bound_service_account_role(test_admin_role: Role) -> Role:
    workspace_id = test_admin_role.workspace_id
    organization_id = test_admin_role.organization_id
    assert workspace_id is not None
    assert organization_id is not None
    return Role(
        type="service_account",
        service_id="tracecat-api",
        organization_id=organization_id,
        workspace_id=workspace_id,
        bound_workspace_id=workspace_id,
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"workflow:read", "workspace:read"}),
    )


@pytest.fixture
def mock_workflow(test_workspace: Workspace) -> Workflow:
    return Workflow(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
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
async def test_service_account_can_list_workflows(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
    mock_workflow: Workflow,
) -> None:
    mock_definition = WorkflowDefinitionMinimal(
        id=str(uuid.uuid4()),
        version=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    mock_response = CursorPaginatedResponse(
        items=[(mock_workflow, mock_definition)],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )

    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_workflows.return_value = mock_response
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(workspace_targeted_service_account_role)
        try:
            response = client.get(
                "/workflows",
                params={
                    "workspace_id": str(
                        workspace_targeted_service_account_role.workspace_id
                    )
                },
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["title"] == "Test Workflow"


@pytest.mark.anyio
async def test_service_account_can_get_workflow(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
    mock_workflow: Workflow,
    mock_webhook: Webhook,
) -> None:
    mock_workflow.webhook = mock_webhook
    mock_workflow.schedules = []

    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_workflow.return_value = mock_workflow
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(workspace_targeted_service_account_role)
        try:
            response = client.get(
                f"/workflows/{mock_workflow.id}",
                params={
                    "workspace_id": str(
                        workspace_targeted_service_account_role.workspace_id
                    )
                },
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["alias"] == "test-workflow"


@pytest.mark.anyio
async def test_workspace_service_account_can_list_its_workspace(
    client: TestClient,
    workspace_bound_service_account_role: Role,
) -> None:
    workspace = SimpleNamespace(
        id=workspace_bound_service_account_role.workspace_id,
        name="Bound workspace",
    )

    with patch.object(workspaces_router, "WorkspaceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_accessible_workspaces.return_value = [workspace]
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(workspace_bound_service_account_role)
        try:
            response = client.get("/workspaces")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "id": str(workspace_bound_service_account_role.workspace_id),
            "name": "Bound workspace",
        }
    ]


@pytest.mark.anyio
async def test_org_service_account_can_list_org_workspaces(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    organization_id = test_admin_role.organization_id
    assert organization_id is not None
    role = Role(
        type="service_account",
        service_id="tracecat-api",
        organization_id=organization_id,
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"org:workspace:read"}),
    )
    workspaces = [
        SimpleNamespace(id=uuid.uuid4(), name="Alpha"),
        SimpleNamespace(id=uuid.uuid4(), name="Beta"),
    ]

    with patch.object(workspaces_router, "WorkspaceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_accessible_workspaces.return_value = workspaces
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role)
        try:
            response = client.get("/workspaces")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert [item["name"] for item in response.json()] == ["Alpha", "Beta"]


@pytest.mark.anyio
async def test_org_service_account_can_list_workflows_for_target_workspace(
    client: TestClient,
    test_admin_role: Role,
    mock_workflow: Workflow,
) -> None:
    organization_id = test_admin_role.organization_id
    workspace_id = test_admin_role.workspace_id
    assert organization_id is not None
    assert workspace_id is not None

    role = Role(
        type="service_account",
        service_id="tracecat-api",
        organization_id=organization_id,
        workspace_id=workspace_id,
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"workflow:read"}),
    )
    mock_definition = WorkflowDefinitionMinimal(
        id=str(uuid.uuid4()),
        version=1,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    mock_response = CursorPaginatedResponse(
        items=[(mock_workflow, mock_definition)],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )

    with patch.object(
        workflow_management_router, "WorkflowsManagementService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_workflows.return_value = mock_response
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role)
        try:
            response = client.get(
                "/workflows",
                params={"workspace_id": str(workspace_id)},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["title"] == "Test Workflow"


@pytest.mark.anyio
async def test_workspace_list_rejects_service_account_without_required_scope(
    client: TestClient,
    workspace_bound_service_account_role: Role,
) -> None:
    role_without_workspace_access = workspace_bound_service_account_role.model_copy(
        update={"scopes": frozenset({"workflow:read"})}
    )

    token = ctx_role.set(role_without_workspace_access)
    try:
        response = client.get("/workspaces")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_service_account_can_list_workflow_executions_without_user_filter(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
) -> None:
    mock_service = AsyncMock()
    mock_service.list_executions.return_value = []

    with patch.object(
        WorkflowExecutionsService, "connect", new_callable=AsyncMock
    ) as mock_connect:
        mock_connect.return_value = mock_service

        token = ctx_role.set(workspace_targeted_service_account_role)
        try:
            response = client.get(
                "/workflow-executions",
                params={
                    "workspace_id": str(
                        workspace_targeted_service_account_role.workspace_id
                    )
                },
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


@pytest.mark.anyio
async def test_service_account_can_search_workflow_executions_without_user_filter(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
) -> None:
    mock_service = AsyncMock()
    mock_service.list_executions_paginated.return_value = CursorPaginatedResponse(
        items=[],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )

    with patch.object(
        WorkflowExecutionsService, "connect", new_callable=AsyncMock
    ) as mock_connect:
        mock_connect.return_value = mock_service

        token = ctx_role.set(workspace_targeted_service_account_role)
        try:
            response = client.get(
                "/workflow-executions/search",
                params={
                    "workspace_id": str(
                        workspace_targeted_service_account_role.workspace_id
                    )
                },
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/workflow-executions", "/workflow-executions/search"]
)
async def test_workflow_execution_user_filter_rejects_service_account(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
    path: str,
) -> None:
    with patch.object(
        WorkflowExecutionsService, "connect", new_callable=AsyncMock
    ) as mock_connect:
        mock_connect.return_value = AsyncMock()

        token = ctx_role.set(workspace_targeted_service_account_role)
        try:
            response = client.get(
                path,
                params={
                    "workspace_id": str(
                        workspace_targeted_service_account_role.workspace_id
                    ),
                    "user_id": str(uuid.uuid4()),
                },
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["detail"]
        == "user_id filter is not supported for service accounts"
    )


@pytest.mark.anyio
async def test_graph_patch_requires_workflow_update_scope_for_service_account(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
) -> None:
    role_without_update_scope = workspace_targeted_service_account_role.model_copy(
        update={"scopes": frozenset({"workflow:read"})}
    )

    token = ctx_role.set(role_without_update_scope)
    try:
        response = client.patch(
            "/workflows/wf_testworkflow/graph",
            json={"base_version": 1, "operations": []},
            params={
                "workspace_id": str(
                    workspace_targeted_service_account_role.workspace_id
                )
            },
        )
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_service_account_can_list_providers(
    client: TestClient,
    workspace_targeted_service_account_role: Role,
) -> None:
    role = workspace_targeted_service_account_role.model_copy(
        update={"scopes": frozenset({"integration:read"})}
    )
    provider = SimpleNamespace(
        id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        metadata=SimpleNamespace(
            name="Slack",
            description="Slack provider",
            requires_config=True,
            enabled=True,
        ),
    )

    with (
        patch.object(integrations_router, "IntegrationService") as mock_service_cls,
        patch.object(integrations_router, "all_providers", return_value=[provider]),
    ):
        mock_svc = AsyncMock()
        mock_svc.list_integrations.return_value = []
        mock_svc.list_custom_providers.return_value = []
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role)
        try:
            response = client.get(
                "/providers",
                params={"workspace_id": str(role.workspace_id)},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["id"] == "slack"


def test_integration_route_role_boundaries_are_explicit() -> None:
    list_integrations_role = get_type_hints(
        integrations_router.list_integrations, include_extras=True
    )["role"]
    list_providers_role = get_type_hints(
        integrations_router.list_providers, include_extras=True
    )["role"]
    test_connection_role = get_type_hints(
        integrations_router.test_connection, include_extras=True
    )["role"]

    assert list_integrations_role == WorkspaceUserRole
    assert list_providers_role == WorkspaceActorRole
    assert test_connection_role == WorkspaceActorRole


def test_cases_route_role_boundary_remains_user_only() -> None:
    list_cases_role = get_type_hints(cases_router.list_cases, include_extras=True)[
        "role"
    ]

    assert list_cases_role == cases_router.WorkspaceUser
