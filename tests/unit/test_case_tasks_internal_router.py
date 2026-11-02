from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.cases import internal_router
from tracecat.cases.dependencies import ExecutorCaseIDPath
from tracecat.db.engine import get_async_session
from tracecat.exceptions import TracecatNotFoundError
from tracecat.executor.action_gateway.app import create_app
from tracecat.executor.action_gateway.policy import (
    enforce_agent_script_gateway_access,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.executions.correlation import build_case_task_correlation_id

CASE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
TASK_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
WORKFLOW_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
RUN_URL = f"/internal/cases/{CASE_ID}/tasks/{TASK_ID}/run"


def _definition(expects: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        content={
            "title": "Contain host",
            "description": "Contain a host",
            "entrypoint": {"expects": expects, "ref": "start"},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "hello"},
                }
            ],
            "config": {"scheduler": "static"},
        },
        registry_lock=None,
    )


@dataclass
class Gateway:
    client: TestClient
    task_service: MagicMock
    definitions_service: MagicMock
    exec_service: MagicMock


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[Gateway]:
    task_service = MagicMock()
    task_service.get_task = AsyncMock(
        return_value=SimpleNamespace(
            id=TASK_ID,
            case_id=CASE_ID,
            workflow_id=WORKFLOW_ID,
            default_trigger_values={"host": "default-host", "reason": "default"},
        )
    )
    definitions_service = MagicMock()
    definitions_service.get_definition_by_workflow_id = AsyncMock(
        return_value=_definition()
    )
    exec_service = MagicMock()
    exec_service.create_workflow_execution_wait_for_start = AsyncMock(
        return_value={
            "message": "Workflow execution started",
            "wf_id": WorkflowUUID.new(WORKFLOW_ID),
            "wf_exec_id": "wf-exec-123",
        }
    )

    app = create_app()
    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = lambda: (
        Role(
            type="service",
            service_id="tracecat-executor",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scopes=frozenset({"case:read", "workflow:execute"}),
        )
    )
    app.dependency_overrides[get_args(ExecutorCaseIDPath)[1].dependency] = lambda: (
        CASE_ID
    )
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    app.dependency_overrides[enforce_agent_script_gateway_access] = lambda: None

    monkeypatch.setattr(
        internal_router, "CaseTasksService", lambda *_args, **_kwargs: task_service
    )
    monkeypatch.setattr(
        internal_router,
        "WorkflowDefinitionsService",
        lambda *_args, **_kwargs: definitions_service,
    )
    monkeypatch.setattr(
        internal_router.WorkflowExecutionsService,
        "connect",
        AsyncMock(return_value=exec_service),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Gateway(client, task_service, definitions_service, exec_service)


def test_run_task_starts_linked_workflow(gateway: Gateway) -> None:
    response = gateway.client.post(RUN_URL, json={"inputs": {"reason": "analyst"}})

    assert response.status_code == 201
    assert response.json() == {
        "task_id": str(TASK_ID),
        "workflow_id": WorkflowUUID.new(WORKFLOW_ID).short(),
        "workflow_execution_id": "wf-exec-123",
        "message": "Workflow execution started",
    }
    call = gateway.exec_service.create_workflow_execution_wait_for_start.await_args
    assert call.kwargs["wf_id"] == WorkflowUUID.new(WORKFLOW_ID)
    assert call.kwargs["payload"] == {
        "host": "default-host",
        "reason": "analyst",
        "case_id": str(CASE_ID),
        "task_id": str(TASK_ID),
    }
    assert call.kwargs["correlation_id"] == build_case_task_correlation_id(TASK_ID)
    assert call.kwargs["registry_lock"] is None


def test_run_task_without_inputs_uses_defaults(gateway: Gateway) -> None:
    response = gateway.client.post(RUN_URL, json={})

    assert response.status_code == 201
    call = gateway.exec_service.create_workflow_execution_wait_for_start.await_args
    assert call.kwargs["payload"]["reason"] == "default"


def test_run_task_rejects_task_from_another_case(gateway: Gateway) -> None:
    gateway.task_service.get_task.return_value.case_id = uuid.uuid4()

    response = gateway.client.post(RUN_URL, json={})

    assert response.status_code == 404
    gateway.exec_service.create_workflow_execution_wait_for_start.assert_not_awaited()


def test_run_task_returns_404_for_missing_task(gateway: Gateway) -> None:
    gateway.task_service.get_task.side_effect = TracecatNotFoundError("missing")

    response = gateway.client.post(RUN_URL, json={})

    assert response.status_code == 404


def test_run_task_requires_workflow(gateway: Gateway) -> None:
    gateway.task_service.get_task.return_value.workflow_id = None

    response = gateway.client.post(RUN_URL, json={})

    assert response.status_code == 400
    assert "no associated workflow" in response.json()["detail"]


def test_run_task_requires_published_definition(gateway: Gateway) -> None:
    gateway.definitions_service.get_definition_by_workflow_id.return_value = None

    response = gateway.client.post(RUN_URL, json={})

    assert response.status_code == 404
    assert "no published definition" in response.json()["detail"]


def test_run_task_validates_trigger_inputs(gateway: Gateway) -> None:
    gateway.definitions_service.get_definition_by_workflow_id.return_value = (
        _definition({"device_id": {"type": "str"}})
    )

    response = gateway.client.post(RUN_URL, json={})

    assert response.status_code == 400
    gateway.exec_service.create_workflow_execution_wait_for_start.assert_not_awaited()
