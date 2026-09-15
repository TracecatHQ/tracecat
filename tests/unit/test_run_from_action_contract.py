"""The replay endpoint publishes its handled errors to API clients."""

import uuid
from collections.abc import Iterator
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.workflow.executions.router import router
from tracecat.workflow.management.management import WorkflowsManagementService


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"workflow:execute"}),
    )
    role_dependency = get_args(WorkspaceUserRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: role
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def run_from_action(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    service = MagicMock(spec=WorkflowsManagementService)
    run = AsyncMock()
    service.run_workflow_from_action = run
    session = AsyncMock()
    session.__aenter__.return_value = service
    monkeypatch.setattr(
        WorkflowsManagementService, "with_session", MagicMock(return_value=session)
    )
    return run


def test_run_from_action_declares_handled_errors() -> None:
    app = FastAPI()
    app.include_router(router)
    operations = app.openapi()["paths"]
    path = next(path for path in operations if path.endswith("/draft/from-action"))
    responses = operations[path]["post"]["responses"]
    assert {"200", "400", "404", "422"} <= responses.keys()


@pytest.mark.parametrize(
    "code",
    ["unknown_action_ref", "unresolved_parents", "source_inputs_not_inline"],
)
def test_run_from_action_preserves_validation_error_detail(
    client: TestClient, run_from_action: AsyncMock, code: str
) -> None:
    run_from_action.side_effect = TracecatValidationError(
        "Cannot replay this action.", detail={"code": code, "parent_refs": ["upstream"]}
    )
    wf_id = WorkflowUUID.new_uuid4()
    response = client.post(
        "/workflow-executions/draft/from-action",
        json={
            "workflow_id": str(wf_id),
            "action_ref": "start",
            "source_execution_id": generate_exec_id(wf_id),
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "type": "TracecatValidationError",
            "message": "Cannot replay this action.",
            "detail": {"code": code, "parent_refs": ["upstream"]},
        }
    }
    run_from_action.assert_awaited_once()


def test_run_from_action_preserves_not_found_error(
    client: TestClient, run_from_action: AsyncMock
) -> None:
    run_from_action.side_effect = TracecatNotFoundError("Source execution not found")
    wf_id = WorkflowUUID.new_uuid4()
    response = client.post(
        "/workflow-executions/draft/from-action",
        json={
            "workflow_id": str(wf_id),
            "action_ref": "start",
            "source_execution_id": generate_exec_id(wf_id),
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Source execution not found"}
    run_from_action.assert_awaited_once()
