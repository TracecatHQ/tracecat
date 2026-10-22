from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatServiceError,
    TracecatValidationError,
)
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.schedules import router as schedules_router


@pytest.mark.anyio
async def test_schedule_search_reaches_static_route(
    client: TestClient, test_admin_role: Role
) -> None:
    session = AsyncMock()
    result = Mock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    assert isinstance(client.app, FastAPI)
    with patch.dict(
        client.app.dependency_overrides, {get_async_session: lambda: session}
    ):
        response = client.get("/schedules/search")
    assert response.status_code == 200
    assert response.json() == []
    session.execute.assert_awaited_once()
    assert session.execute.await_args is not None
    statement = session.execute.await_args.args[0]
    assert test_admin_role.workspace_id in statement.compile().params.values()


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("schedule_id", ["not-an-id", "sch_!", "sch-invalid"])
async def test_schedule_invalid_id_is_validation_error(
    client: TestClient, test_admin_role: Role, method: str, schedule_id: str
) -> None:
    with patch.object(schedules_router, "WorkflowSchedulesService") as service:
        response = client.request(method, f"/schedules/{schedule_id}", json={})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "schedule_id"]
    service.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "fields",
    [
        {"cron": ""},
        {"cron": "   "},
        {"every": "PT0S"},
        {"every": "-PT1S"},
        {"every": "PT0.5S"},
        {"every": "PT1S", "offset": "-PT1S"},
        {"every": "PT1S", "offset": "PT1S"},
        {"every": "PT1S", "offset": "PT2S"},
        {"cron": "0 0 * * *", "every": "PT1S"},
        {"cron": "0 0 * * *", "timeout": -1},
        {"cron": "0 0 * * *", "timeout": "NaN"},
        {"cron": "0 0 * * *", "timeout": "Infinity"},
        {"cron": "0 0 * * *", "status": None},
    ],
)
async def test_schedule_invalid_inputs_do_not_reach_persistence(
    client: TestClient,
    test_admin_role: Role,
    operation: str,
    fields: dict[str, str | int | None],
) -> None:
    payload = dict(fields)
    path = f"/schedules/{ScheduleUUID.new_uuid4()}"
    if operation == "create":
        payload["workflow_id"] = str(WorkflowUUID.new_uuid4())
        path = "/schedules"
    with patch.object(schedules_router, "WorkflowSchedulesService") as service:
        response = client.post(path, json=payload)
    assert response.status_code == 422
    service.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        ("GET", "/schedules", "list_schedules"),
        ("POST", "/schedules", "create_schedule"),
        ("GET", "/schedules/{id}", "get_schedule"),
        ("POST", "/schedules/{id}", "update_schedule"),
        ("DELETE", "/schedules/{id}", "delete_schedule"),
    ],
)
@pytest.mark.parametrize("error_type", [IntegrityError, RuntimeError])
async def test_schedule_unexpected_failures_are_sanitized(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    operation: str,
    error_type: type[IntegrityError] | type[RuntimeError],
) -> None:
    sentinel = "synthetic-private-schedule-diagnostic"
    error = (
        IntegrityError("synthetic statement", {}, Exception(sentinel))
        if error_type is IntegrityError
        else RuntimeError(sentinel)
    )
    service = AsyncMock()
    operation_mock = AsyncMock(side_effect=error)
    setattr(service, operation, operation_mock)
    with patch.object(
        schedules_router, "WorkflowSchedulesService", return_value=service
    ):
        response = client.request(
            method,
            path.format(id=ScheduleUUID.new_uuid4()),
            json={"workflow_id": str(WorkflowUUID.new_uuid4()), "every": "PT1S"},
        )
    assert response.status_code == 500
    assert sentinel not in response.text
    operation_mock.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation", ["create_schedule", "update_schedule", "delete_schedule"]
)
async def test_schedule_service_errors_are_sanitized(
    client: TestClient, test_admin_role: Role, operation: str
) -> None:
    service = AsyncMock()
    setattr(
        service,
        operation,
        AsyncMock(
            side_effect=TracecatServiceError("synthetic-private-schedule-diagnostic")
        ),
    )
    path = (
        "/schedules"
        if operation == "create_schedule"
        else f"/schedules/{ScheduleUUID.new_uuid4()}"
    )
    method = "DELETE" if operation == "delete_schedule" else "POST"
    with patch.object(
        schedules_router, "WorkflowSchedulesService", return_value=service
    ):
        response = client.request(
            method,
            path,
            json={"workflow_id": str(WorkflowUUID.new_uuid4()), "every": "PT1S"},
        )
    assert response.status_code == 500
    assert "synthetic-private-schedule-diagnostic" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation", ["get_schedule", "update_schedule", "delete_schedule"]
)
async def test_missing_schedule_remains_not_found(
    client: TestClient, test_admin_role: Role, operation: str
) -> None:
    service = AsyncMock()
    setattr(
        service,
        operation,
        AsyncMock(side_effect=TracecatNotFoundError("Schedule not found")),
    )
    method = {
        "get_schedule": "GET",
        "update_schedule": "POST",
        "delete_schedule": "DELETE",
    }[operation]
    with patch.object(
        schedules_router, "WorkflowSchedulesService", return_value=service
    ):
        response = client.request(
            method, f"/schedules/{ScheduleUUID.new_uuid4()}", json={}
        )
    assert response.status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (TracecatNotFoundError("Workflow not found"), 404),
        (TracecatConflictError("Workflow must be published"), 409),
    ],
)
async def test_schedule_create_preserves_missing_and_unpublished_workflow_errors(
    client: TestClient, test_admin_role: Role, error: Exception, expected_status: int
) -> None:
    service = AsyncMock()
    service.create_schedule.side_effect = error
    with patch.object(
        schedules_router, "WorkflowSchedulesService", return_value=service
    ):
        response = client.post(
            "/schedules",
            json={"workflow_id": str(WorkflowUUID.new_uuid4()), "every": "PT1S"},
        )
    assert response.status_code == expected_status
    assert response.json()["detail"] == str(error)


@pytest.mark.anyio
async def test_schedule_update_preserves_typed_validation_detail(
    client: TestClient, test_admin_role: Role
) -> None:
    service = AsyncMock()
    service.update_schedule.side_effect = TracecatValidationError(
        "Either cron or every must be provided for a schedule"
    )
    with patch.object(
        schedules_router, "WorkflowSchedulesService", return_value=service
    ):
        response = client.post(
            f"/schedules/{ScheduleUUID.new_uuid4()}", json={"every": None}
        )
    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Either cron or every must be provided for a schedule"
    )
