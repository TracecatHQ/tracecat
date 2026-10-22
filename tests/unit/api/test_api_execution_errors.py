from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode

from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.executions import internal_router, router
from tracecat.workflow.executions.service import (
    WorkflowExecutionNotFoundError,
    WorkflowExecutionsService,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("internal", "method", "suffix"),
    [
        (False, "GET", ""),
        (False, "GET", "/reset-points"),
        (False, "POST", "/reset"),
        (False, "POST", "/cancel"),
        (False, "POST", "/terminate"),
        (True, "GET", ""),
    ],
)
@pytest.mark.parametrize(
    ("rpc_status", "expected_status"),
    [
        (RPCStatusCode.NOT_FOUND, 404),
        (RPCStatusCode.UNAVAILABLE, 500),
        (RPCStatusCode.PERMISSION_DENIED, 500),
    ],
)
async def test_execution_lookup_uses_rpc_status_not_message(
    client: TestClient,
    action_gateway_client: TestClient,
    test_admin_role: Role,
    internal: bool,
    method: str,
    suffix: str,
    rpc_status: RPCStatusCode,
    expected_status: int,
) -> None:
    handle = Mock(spec=WorkflowHandle)
    sentinel = (
        "synthetic-private-diagnostic"
        if rpc_status == RPCStatusCode.NOT_FOUND
        else "synthetic-private-not found-diagnostic"
    )
    handle.describe = AsyncMock(side_effect=RPCError(sentinel, rpc_status, b""))
    temporal = Mock(spec=Client)
    temporal.get_workflow_handle_for.return_value = handle
    service = WorkflowExecutionsService(client=temporal, role=test_admin_role)
    selected_client = action_gateway_client if internal else client
    path = (
        "/internal/workflows/executions/wf_synthetic/exec_synthetic"
        if internal
        else "/workflow-executions/wf_synthetic:exec_synthetic"
    )
    with (
        patch.object(
            WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
        ),
        patch.object(AuditService, "with_session") as audit,
    ):
        audit.return_value.__aenter__.return_value.create_event = AsyncMock()
        response = selected_client.request(method, path + suffix, json={})
    assert response.status_code == expected_status
    assert sentinel not in response.text
    handle.describe.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic-private-execution-diagnostic"),
        RPCError(
            "synthetic-private-execution-diagnostic", RPCStatusCode.UNAVAILABLE, b""
        ),
        IntegrityError(
            "synthetic statement",
            {},
            Exception("synthetic-private-execution-diagnostic"),
        ),
    ],
)
async def test_internal_status_contains_unknown_errors(
    action_gateway_client: TestClient, test_admin_role: Role, error: Exception
) -> None:
    service = AsyncMock()
    service.get_execution.side_effect = error
    with patch.object(
        WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
    ):
        response = action_gateway_client.get(
            "/internal/workflows/executions/wf_synthetic/exec_synthetic"
        )
    assert response.status_code == 500
    assert "synthetic-private-execution-diagnostic" not in response.text


@pytest.mark.anyio
async def test_internal_status_classifies_direct_rpc_not_found(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    service = AsyncMock()
    service.get_execution.side_effect = RPCError(
        "synthetic-diagnostic", RPCStatusCode.NOT_FOUND, b""
    )
    with patch.object(
        WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
    ):
        response = action_gateway_client.get(
            "/internal/workflows/executions/wf_synthetic/exec_synthetic"
        )
    assert response.status_code == 404
    assert "synthetic-diagnostic" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["definition", "dispatch", "lookup"])
async def test_internal_execute_contains_unknown_errors(
    action_gateway_client: TestClient, test_admin_role: Role, stage: str
) -> None:
    sentinel = "synthetic-private-execution-diagnostic"
    definition_service = AsyncMock()
    definition_service.get_definition_by_workflow_id.return_value = SimpleNamespace(
        content={}, registry_lock=None
    )
    if stage == "lookup":
        definition_service.get_definition_by_workflow_id.side_effect = IntegrityError(
            "synthetic statement", {}, Exception(sentinel)
        )
    with (
        patch.object(
            internal_router,
            "WorkflowDefinitionsService",
            return_value=definition_service,
        ),
        patch.object(internal_router, "DSLInput", side_effect=RuntimeError(sentinel))
        if stage == "definition"
        else patch.object(
            internal_router, "DSLInput", return_value=Mock(spec=DSLInput)
        ),
        patch.object(
            WorkflowExecutionsService,
            "connect",
            AsyncMock(side_effect=RuntimeError(sentinel)),
        ),
    ):
        response = action_gateway_client.post(
            "/internal/workflows/executions",
            json={"workflow_id": str(WorkflowUUID.new_uuid4())},
        )
    assert response.status_code == 500
    assert sentinel not in response.text


@pytest.mark.anyio
async def test_internal_execute_preserves_typed_validation(
    action_gateway_client: TestClient, test_admin_role: Role
) -> None:
    definition_service = AsyncMock()
    definition_service.get_definition_by_workflow_id.return_value = SimpleNamespace(
        content={}, registry_lock=None
    )
    service = Mock(spec=WorkflowExecutionsService)
    service.create_workflow_execution_nowait.side_effect = TracecatValidationError(
        "Invalid trigger inputs"
    )
    with (
        patch.object(
            internal_router,
            "WorkflowDefinitionsService",
            return_value=definition_service,
        ),
        patch.object(internal_router, "DSLInput", return_value=Mock(spec=DSLInput)),
        patch.object(
            WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
        ),
    ):
        response = action_gateway_client.post(
            "/internal/workflows/executions",
            json={"workflow_id": str(WorkflowUUID.new_uuid4())},
        )
    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "Invalid trigger inputs"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic-private-reset-diagnostic"),
        IntegrityError(
            "synthetic statement", {}, Exception("synthetic-private-reset-diagnostic")
        ),
    ],
)
async def test_bulk_reset_contains_unexpected_errors(
    client: TestClient, test_admin_role: Role, error: Exception
) -> None:
    service = WorkflowExecutionsService(client=Mock(spec=Client), role=test_admin_role)
    with (
        patch.object(
            router.WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
        ),
        patch.object(service, "reset_workflow_execution", AsyncMock(side_effect=error)),
    ):
        response = client.post(
            "/workflow-executions/reset/bulk",
            json={"execution_ids": ["wf_synthetic/exec_synthetic"]},
        )
    assert response.status_code == 200
    assert (
        response.json()["results"][0]["error"] == "Failed to reset workflow execution"
    )
    assert "synthetic-private-reset-diagnostic" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        WorkflowExecutionNotFoundError("Workflow execution not found"),
        ValueError("No resettable point found for workflow execution start."),
    ],
)
async def test_bulk_reset_preserves_expected_errors(
    client: TestClient, test_admin_role: Role, error: Exception
) -> None:
    service = WorkflowExecutionsService(client=Mock(spec=Client), role=test_admin_role)
    with (
        patch.object(
            WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
        ),
        patch.object(service, "reset_workflow_execution", AsyncMock(side_effect=error)),
    ):
        response = client.post(
            "/workflow-executions/reset/bulk",
            json={"execution_ids": ["wf_synthetic/exec_synthetic"]},
        )
    assert response.status_code == 200
    assert response.json()["results"][0]["error"] == str(error)
