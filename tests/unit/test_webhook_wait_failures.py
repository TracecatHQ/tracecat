"""Exercise webhook failure responses through FastAPI and the Sentry integration."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from unittest.mock import AsyncMock, patch

import orjson
import pytest
import sentry_sdk
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError

from tracecat.api.common import generic_exception_handler
from tracecat.db.models import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.observability import sentry as sentry_module
from tracecat.observability.sentry import SentryTag, initialize_api_sentry
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import (
    application_error_from_classification,
    build_error_transport_detail,
)
from tracecat.webhooks.dependencies import (
    validate_incoming_webhook,
    validate_workflow_definition,
)
from tracecat.webhooks.router import router
from tracecat.webhooks.schemas import WebhookWaitErrorResponse
from tracecat.workflow.executions.service import WorkflowExecutionsService

_WORKFLOW_ID = WorkflowUUID.new_uuid4()
_WEBHOOK_PATH = f"/webhooks/{_WORKFLOW_ID.short()}/synthetic-secret/wait"
_PRIVATE_VALUE = "synthetic-private-workflow-value"
_USER_ERROR = RuntimeErrorClassification.user(
    kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
    message=_PRIVATE_VALUE,
    retry_disposition=RetryDisposition.NON_RETRYABLE,
)
_PLATFORM_ERROR = RuntimeErrorClassification.platform(
    kind=RuntimeErrorKind.RUNTIME_UNCLASSIFIED,
    message="Workflow runtime failed",
    retry_disposition=RetryDisposition.NON_RETRYABLE,
)


class _InMemoryTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        if event := envelope.get_event():
            self.events.append(event)


@dataclass(frozen=True, slots=True)
class _WebhookHarness:
    client: TestClient
    execute: AsyncMock
    sentry_events: list[Event]


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch) -> Iterator[_WebhookHarness]:
    monkeypatch.setattr(sentry_module.config, "TRACECAT__SERVICE_NAME", "api")
    transport = _InMemoryTransport()
    initialize_api_sentry(
        dsn="https://public@example.com/1",
        environment="test",
        release="tracecat@test",
        transport=transport,
    )
    service = AsyncMock(spec=WorkflowExecutionsService)
    monkeypatch.setattr(
        WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
    )
    definition = WorkflowDefinition(
        content=DSLInput.model_validate(
            {
                "title": "Synthetic webhook workflow",
                "description": "Webhook failure regression test",
                "entrypoint": {"ref": "start"},
                "actions": [{"ref": "start", "action": "core.noop"}],
            }
        ).model_dump(mode="json"),
        registry_lock=None,
    )
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(Exception, generic_exception_handler)
    app.dependency_overrides[validate_incoming_webhook] = lambda: None
    app.dependency_overrides[validate_workflow_definition] = lambda: definition
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield _WebhookHarness(
                client, service.create_workflow_execution, transport.events
            )
    finally:
        sentry_sdk.flush()
        sentry_sdk.init(
            dsn=None, default_integrations=False, auto_enabling_integrations=False
        )


@pytest.mark.parametrize("unwrap", [False, True])
@pytest.mark.parametrize("workflow_fails", [False, True])
def test_trace_annotation_failure_does_not_block_workflow(
    webhook: _WebhookHarness, unwrap: bool, workflow_fails: bool
) -> None:
    if workflow_fails:
        webhook.execute.side_effect = WorkflowFailureError(
            cause=application_error_from_classification(_USER_ERROR)
        )
    else:
        webhook.execute.return_value = {"result": InlineObject(data={"ok": True})}

    with patch(
        "tracecat.webhooks.router.set_current_span_attributes",
        side_effect=RuntimeError(_PRIVATE_VALUE),
    ) as annotate:
        response = webhook.client.post(
            _WEBHOOK_PATH, json={}, params={"unwrap": unwrap}
        )

    sentry_sdk.flush()
    annotate.assert_called_once()
    webhook.execute.assert_awaited_once()
    if workflow_fails:
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == _USER_ERROR.kind.value
    else:
        assert response.status_code == 200
        assert response.json() == (
            {"ok": True} if unwrap else {"kind": "value", "value": {"ok": True}}
        )
    assert _PRIVATE_VALUE not in response.text
    assert webhook.sentry_events == []


@pytest.mark.parametrize("unwrap", [False, True])
@pytest.mark.parametrize("terminal_map", [False, True], ids=["direct", "terminal-map"])
@pytest.mark.parametrize(
    "kind",
    [
        RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        RuntimeErrorKind.WORKFLOW_TRIGGER_INPUT_INVALID,
    ],
)
def test_user_failure_returns_safe_422_without_sentry(
    webhook: _WebhookHarness,
    kind: RuntimeErrorKind,
    terminal_map: bool,
    unwrap: bool,
) -> None:
    classification = _USER_ERROR.model_copy(update={"kind": kind})
    detail = build_error_transport_detail(
        classification, diagnostic={"message": _PRIVATE_VALUE}
    ).model_dump(mode="json")
    cause = application_error_from_classification(
        classification, {"http_request": detail} if terminal_map else detail
    )
    webhook.execute.side_effect = WorkflowFailureError(cause=cause)

    response = webhook.client.post(_WEBHOOK_PATH, json={}, params={"unwrap": unwrap})
    sentry_sdk.flush()

    assert response.status_code == 422
    webhook.execute.assert_awaited_once()
    assert response.json() == {
        "detail": {
            "code": kind.value,
            "wf_exec_id": webhook.execute.call_args.kwargs["wf_exec_id"],
            "message": "Workflow execution failed. Check the workflow run for details.",
        }
    }
    assert _PRIVATE_VALUE not in response.text
    assert WebhookWaitErrorResponse.model_validate(response.json())
    assert webhook.sentry_events == []


@pytest.mark.parametrize(
    "cause",
    [
        pytest.param(
            application_error_from_classification(_PLATFORM_ERROR), id="platform"
        ),
        pytest.param(ApplicationError(_PRIVATE_VALUE), id="unclassified"),
        pytest.param(
            ApplicationError(
                _PRIVATE_VALUE,
                {"owner": "user"},
                type=RuntimeErrorKind.ACTION_EXECUTION_FAILED.value,
            ),
            id="unclassified-with-user-like-type",
        ),
        pytest.param(
            application_error_from_classification(
                _USER_ERROR,
                {
                    "first": build_error_transport_detail(_USER_ERROR).model_dump(
                        mode="json"
                    ),
                    "second": build_error_transport_detail(_PLATFORM_ERROR).model_dump(
                        mode="json"
                    ),
                },
            ),
            id="mixed-user-and-platform",
        ),
    ],
)
def test_platform_or_unclassified_failure_still_returns_500_and_reports(
    webhook: _WebhookHarness, cause: ApplicationError
) -> None:
    webhook.execute.side_effect = WorkflowFailureError(cause=cause)

    response = webhook.client.post(_WEBHOOK_PATH, json={})
    sentry_sdk.flush()

    assert response.status_code == 500
    assert len(webhook.sentry_events) == 1
    event = webhook.sentry_events[0]
    assert "tags" in event
    assert event["tags"][SentryTag.ERROR_OWNER.value] == "platform"
    assert _PRIVATE_VALUE not in response.text
    assert _PRIVATE_VALUE not in orjson.dumps(webhook.sentry_events).decode()


def test_incidental_user_error_does_not_hide_unclassified_failure(
    webhook: _WebhookHarness,
) -> None:
    cause = ApplicationError(_PRIVATE_VALUE)
    cause.__context__ = application_error_from_classification(_USER_ERROR)
    webhook.execute.side_effect = WorkflowFailureError(cause=cause)

    response = webhook.client.post(_WEBHOOK_PATH, json={})
    sentry_sdk.flush()

    assert response.status_code == 500
    assert len(webhook.sentry_events) == 1


def test_request_validation_keeps_422_without_executing_workflow(
    webhook: _WebhookHarness,
) -> None:
    response = webhook.client.post(_WEBHOOK_PATH, json={}, params={"unwrap": "invalid"})
    sentry_sdk.flush()

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    parsed = WebhookWaitErrorResponse.model_validate(response.json())
    assert parsed.model_dump(exclude_unset=True) == response.json()
    webhook.execute.assert_not_awaited()
    assert webhook.sentry_events == []


def test_request_validation_preserves_validator_context() -> None:
    app = FastAPI()

    def constrained_query(limit: Annotated[int, Query(gt=0)]) -> int:
        return limit

    app.add_api_route("/", constrained_query, methods=["GET"])
    with TestClient(app) as client:
        response = client.get("/", params={"limit": "0"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["ctx"] == {"gt": 0}
    parsed = WebhookWaitErrorResponse.model_validate(response.json())
    assert parsed.model_dump(exclude_unset=True) == response.json()


def test_openapi_documents_workflow_and_request_validation_errors(
    webhook: _WebhookHarness,
) -> None:
    spec = webhook.client.get("/openapi.json").json()
    response_schema = spec["paths"]["/webhooks/{workflow_id}/{secret}/wait"]["post"][
        "responses"
    ]["422"]["content"]["application/json"]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/WebhookWaitErrorResponse"}
    detail_schema = spec["components"]["schemas"]["WebhookWaitErrorResponse"][
        "properties"
    ]["detail"]
    assert detail_schema["anyOf"] == [
        {"$ref": "#/components/schemas/WebhookWaitFailureDetail"},
        {
            "type": "array",
            "items": {"$ref": "#/components/schemas/WebhookRequestValidationError"},
        },
    ]
    validation_schema = spec["components"]["schemas"]["WebhookRequestValidationError"]
    assert {"input", "ctx"} <= validation_schema["properties"].keys()
    assert validation_schema["required"] == ["loc", "msg", "type"]
