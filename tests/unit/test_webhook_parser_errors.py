"""Webhook parsing failures keep client errors separate from unexpected failures."""

from collections.abc import Iterator
from json import JSONDecodeError
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from python_multipart.exceptions import QuerystringParseError
from starlette.formparsers import MultiPartException

from tests._forms_utils import dsl_with_inputs
from tracecat.api.common import generic_exception_handler
from tracecat.db.models import WorkflowDefinition
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.webhooks.dependencies import (
    validate_incoming_webhook,
    validate_workflow_definition,
)
from tracecat.webhooks.router import router
from tracecat.workflow.executions.service import WorkflowExecutionsService


@pytest.fixture
def webhook_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    workflow_id = WorkflowUUID.new_uuid4()
    definition = WorkflowDefinition(
        content=dsl_with_inputs(None).model_dump(mode="json")
    )
    service = AsyncMock(spec=WorkflowExecutionsService)
    service.create_workflow_execution_wait_for_start.return_value = {
        "message": "Workflow execution created",
        "wf_id": workflow_id,
        "wf_exec_id": generate_exec_id(workflow_id),
    }
    monkeypatch.setattr(
        WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
    )
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(Exception, generic_exception_handler)
    app.dependency_overrides[validate_incoming_webhook] = lambda: None
    app.dependency_overrides[validate_workflow_definition] = lambda: definition
    with TestClient(
        app,
        base_url=f"http://test/webhooks/{workflow_id.short()}/synthetic-secret/",
        raise_server_exceptions=False,
    ) as client:
        yield client


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (QuerystringParseError("synthetic parse failure"), 400),
        (MultiPartException("synthetic parse failure"), 400),
        (RuntimeError("synthetic internal detail"), 500),
        (ValueError("synthetic internal detail"), 500),
    ],
    ids=["querystring", "multipart", "runtime", "unrelated_value_error"],
)
def test_form_parser_error_classification(
    webhook_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    error: Exception,
    expected_status: int,
) -> None:
    monkeypatch.setattr(Request, "form", AsyncMock(side_effect=error))
    response = webhook_client.request(
        method,
        "",
        content=b"key=value",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == expected_status
    assert response.json() == (
        {"detail": "Invalid form data payload"}
        if expected_status == 400
        else {"message": "An unexpected error occurred. Please try again later."}
    )


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_real_form_parser_limit_remains_400(
    webhook_client: TestClient, method: str
) -> None:
    response = webhook_client.request(
        method,
        "",
        content="&".join(f"key{i}=value" for i in range(1001)),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Too many fields. Maximum number of fields is 1000."
    }


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (JSONDecodeError("synthetic parse failure", "", 0), 200),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"), 200),
        (RuntimeError("synthetic internal detail"), 500),
        (ValueError("synthetic internal detail"), 500),
    ],
    ids=["json", "unicode", "runtime", "unrelated_value_error"],
)
def test_echo_json_error_classification(
    webhook_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    error: Exception,
    expected_status: int,
) -> None:
    monkeypatch.setattr(Request, "json", AsyncMock(side_effect=error))
    response = webhook_client.request(
        method,
        "?echo=true",
        content=b"key=value",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["payload"] is None
    else:
        assert response.json() == {
            "message": "An unexpected error occurred. Please try again later."
        }


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_form_payload_and_non_json_echo_remain_supported(
    webhook_client: TestClient, method: str
) -> None:
    response = webhook_client.request(
        method,
        "?echo=true",
        content=b"key=value",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    assert response.json()["payload"] is None
