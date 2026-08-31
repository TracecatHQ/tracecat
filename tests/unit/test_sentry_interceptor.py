from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event
from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import ExecuteWorkflowInput, WorkflowInboundInterceptor

from tracecat.dsl import interceptor as interceptor_module
from tracecat.dsl.interceptor import (
    RuntimeErrorAttributionInterceptor,
    SentryInterceptor,
    _RuntimeErrorAttributionWorkflowInterceptor,
    _SentryWorkflowInterceptor,
    build_workflow_interceptors,
)
from tracecat.logger import logger
from tracecat.observability.sentry import initialize_sentry
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.temporal.errors import application_error_from_classification
from tracecat.workflow.executions.enums import TriggerType

_SENSITIVE_VALUE = "synthetic-sensitive-workflow-payload"


@dataclass(frozen=True, slots=True)
class _WorkflowInfo:
    run_id: str = "00000000-0000-4000-8000-000000000001"
    workflow_type: str = "DSLWorkflow"
    attempt: int = 1


class _RaisingInbound(WorkflowInboundInterceptor):
    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        del input
        raise self._error


class _InMemoryTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        if event := envelope.get_event():
            self.events.append(event)


class _TestWorkflow:
    pass


async def _run_workflow() -> None:
    return None


@pytest.fixture
def sentry_events() -> Iterator[list[Event]]:
    transport = _InMemoryTransport()
    initialize_sentry(
        dsn="https://public@example.com/1",
        environment="test-eu",
        release="tracecat@test",
        service_name="worker",
        transport=transport,
    )
    yield transport.events
    sentry_sdk.flush()
    sentry_sdk.init(
        dsn=None,
        default_integrations=False,
        auto_enabling_integrations=False,
    )


@pytest.fixture
def workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> _WorkflowInfo:
    info = _WorkflowInfo()
    monkeypatch.setattr(workflow, "info", lambda: cast(workflow.Info, info))
    monkeypatch.setattr(workflow.unsafe, "is_replaying", lambda: False)
    monkeypatch.setattr(workflow, "patched", lambda _: True)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda _: None)
    monkeypatch.setattr(
        interceptor_module,
        "get_trigger_type",
        lambda _: TriggerType.MANUAL,
    )
    return info


def _workflow_input() -> ExecuteWorkflowInput:
    return ExecuteWorkflowInput(
        type=_TestWorkflow,
        run_fn=_run_workflow,
        args=(),
        headers={},
    )


def test_worker_interceptor_order_places_sentry_outside_attribution() -> None:
    interceptors = build_workflow_interceptors(sentry_enabled=True)

    assert isinstance(interceptors[0], SentryInterceptor)
    assert isinstance(interceptors[1], RuntimeErrorAttributionInterceptor)


def test_worker_interceptor_order_keeps_attribution_without_sentry() -> None:
    interceptors = build_workflow_interceptors(sentry_enabled=False)

    assert len(interceptors) == 1
    assert isinstance(interceptors[0], RuntimeErrorAttributionInterceptor)


@pytest.mark.anyio
async def test_unclassified_platform_failure_is_attributed_then_captured_once(
    sentry_events: list[Event],
    workflow_runtime: _WorkflowInfo,
) -> None:
    del workflow_runtime
    raw_error = RuntimeError(_SENSITIVE_VALUE)
    attribution = _RuntimeErrorAttributionWorkflowInterceptor(
        _RaisingInbound(raw_error)
    )
    sentry = _SentryWorkflowInterceptor(attribution)

    with pytest.raises(ApplicationError):
        await sentry.execute_workflow(_workflow_input())
    sentry_sdk.flush()

    assert len(sentry_events) == 1
    event = sentry_events[0]
    assert "fingerprint" in event
    assert event["fingerprint"] == [
        "tracecat-runtime-v1",
        RuntimeErrorKind.RUNTIME_UNCLASSIFIED.value,
        "{{ default }}",
    ]
    assert "tags" in event
    assert event["tags"] == {
        "temporal.workflow.attempt": "1",
        "temporal.workflow.type": "DSLWorkflow",
        "tracecat.error.cause_type": "RuntimeError",
        "tracecat.error.kind": RuntimeErrorKind.RUNTIME_UNCLASSIFIED.value,
        "tracecat.error.owner": "platform",
        "tracecat.error.retry_disposition": "non_retryable",
        "tracecat.trigger_type": "manual",
    }
    assert "contexts" in event
    assert event["contexts"]["tracecat_workflow"] == {
        "attempt": 1,
        "run_id": "00000000-0000-4000-8000-000000000001",
        "trigger_type": "manual",
        "type": "DSLWorkflow",
    }
    assert set(event["contexts"]) <= {"runtime", "tracecat_workflow"}
    assert not {"breadcrumbs", "extra", "request", "user"} & event.keys()
    assert _SENSITIVE_VALUE not in json.dumps(event)


@pytest.mark.anyio
async def test_user_failure_is_logged_without_a_sentry_event(
    sentry_events: list[Event],
    workflow_runtime: _WorkflowInfo,
) -> None:
    del workflow_runtime
    classification = RuntimeErrorClassification.user(
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        message="Action failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = application_error_from_classification(classification)
    sentry = _SentryWorkflowInterceptor(_RaisingInbound(error))

    with pytest.raises(ApplicationError):
        await sentry.execute_workflow(_workflow_input())
    sentry_sdk.flush()

    assert sentry_events == []


@pytest.mark.anyio
async def test_reporting_failure_does_not_replace_the_workflow_error(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[Event],
    workflow_runtime: _WorkflowInfo,
) -> None:
    del workflow_runtime
    classification = RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.WORKFLOW_RUNTIME_INVARIANT_VIOLATION,
        message="Workflow runtime invariant failed",
        retry_disposition=RetryDisposition.NON_RETRYABLE,
    )
    error = application_error_from_classification(classification)
    monkeypatch.setattr(
        interceptor_module,
        "_capture_platform_failure",
        lambda *_: (_ for _ in ()).throw(RuntimeError("capture unavailable")),
    )
    sentry = _SentryWorkflowInterceptor(_RaisingInbound(error))

    with pytest.raises(ApplicationError) as raised:
        await sentry.execute_workflow(_workflow_input())

    assert raised.value is error
    assert sentry_events == []


def test_error_logs_are_not_promoted_to_sentry_events(
    sentry_events: list[Event],
) -> None:
    logger.error("Synthetic log error", value=_SENSITIVE_VALUE)
    sentry_sdk.flush()

    assert sentry_events == []
