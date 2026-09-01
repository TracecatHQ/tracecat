from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import Mock

import pytest
import sentry_sdk
from sentry_sdk.client import Client
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint
from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import (
    ExecuteWorkflowInput,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

from tracecat.dsl import interceptor as interceptor_module
from tracecat.dsl.interceptor import (
    RuntimeErrorAttributionInterceptor,
    _RuntimeErrorAttributionWorkflowInterceptor,
)
from tracecat.logger import logger
from tracecat.observability import sentry as sentry_module
from tracecat.observability.sentry import (
    SentryTag,
    _sanitize_platform_event,
    initialize_sentry,
    initialize_sentry_from_environment,
)
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


def test_sentry_keeps_only_the_shutdown_flush_integration(
    sentry_events: list[Event],
) -> None:
    del sentry_events

    client = cast(Client, sentry_sdk.get_client())
    integrations = cast(Mapping[str, object], client.integrations)

    assert client.get_integration(AtexitIntegration) is not None
    assert set(integrations) == {AtexitIntegration.identifier}


@pytest.mark.parametrize("dsn", [None, ""])
def test_sentry_environment_bootstrap_is_disabled_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
    dsn: str | None,
) -> None:
    if dsn is None:
        monkeypatch.delenv("SENTRY_DSN", raising=False)
    else:
        monkeypatch.setenv("SENTRY_DSN", dsn)
    initializer = Mock()
    monkeypatch.setattr(sentry_module, "initialize_sentry", initializer)

    initialize_sentry_from_environment()
    initializer.assert_not_called()


def test_sentry_environment_bootstrap_initializes_shared_worker_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    monkeypatch.setattr(sentry_module, "APP_VERSION", "1.2.3")
    monkeypatch.setattr(sentry_module.config, "TRACECAT__APP_ENV", "production")
    monkeypatch.setattr(
        sentry_module.config,
        "TEMPORAL__CLUSTER_NAMESPACE",
        "eu-cloud",
    )
    monkeypatch.setattr(
        sentry_module.config,
        "SENTRY_ENVIRONMENT_OVERRIDE",
        None,
    )
    monkeypatch.setattr(sentry_module.config, "TRACECAT__SERVICE_NAME", "worker")
    initializer = Mock()
    monkeypatch.setattr(sentry_module, "initialize_sentry", initializer)

    initialize_sentry_from_environment()
    initializer.assert_called_once_with(
        dsn="https://public@example.com/1",
        environment="production-eu-cloud",
        release="tracecat@1.2.3",
        service_name="worker",
    )


def test_sentry_contexts_are_filtered_by_context_and_field() -> None:
    event = cast(
        Event,
        {
            "tags": {SentryTag.ERROR_OWNER.value: "platform"},
            "contexts": {
                "runtime": {
                    "name": "CPython",
                    "version": "3.12.8",
                    "build": _SENSITIVE_VALUE,
                },
                "tracecat_workflow": {
                    "run_id": "00000000-0000-4000-8000-000000000001",
                    "type": "DSLWorkflow",
                    "attempt": 1,
                    "trigger_type": "manual",
                    "payload": _SENSITIVE_VALUE,
                },
                "unreviewed": {"value": _SENSITIVE_VALUE},
            },
        },
    )

    sanitized = _sanitize_platform_event(event, cast(Hint, {}))

    assert sanitized is not None
    assert "contexts" in sanitized
    assert sanitized["contexts"] == {
        "runtime": {"name": "CPython", "version": "3.12.8"},
        "tracecat_workflow": {
            "run_id": "00000000-0000-4000-8000-000000000001",
            "type": "DSLWorkflow",
            "attempt": 1,
            "trigger_type": "manual",
        },
    }
    assert _SENSITIVE_VALUE not in json.dumps(sanitized)


@pytest.mark.anyio
async def test_marker_free_history_preserves_original_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow, "patched", lambda _: False)
    raw_error = RuntimeError("Legacy workflow failure")
    configured = RuntimeErrorAttributionInterceptor()
    inbound_class = configured.workflow_interceptor_class(
        cast(WorkflowInterceptorClassInput, object())
    )

    assert inbound_class is _RuntimeErrorAttributionWorkflowInterceptor
    inbound = inbound_class(_RaisingInbound(raw_error))
    with pytest.raises(RuntimeError) as raised:
        await inbound.execute_workflow(_workflow_input())

    assert raised.value is raw_error


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

    with pytest.raises(ApplicationError):
        await attribution.execute_workflow(_workflow_input())
    sentry_sdk.flush()

    assert len(sentry_events) == 1
    event = sentry_events[0]
    assert "exception" in event
    exception = event["exception"]
    assert "values" in exception
    assert exception["values"][-1]["type"] == "RuntimeError"
    assert "fingerprint" in event
    assert event["fingerprint"] == [
        "tracecat-runtime-v1",
        RuntimeErrorKind.RUNTIME_UNCLASSIFIED.value,
        "{{ default }}",
    ]
    assert "tags" in event
    assert event["tags"] == {
        SentryTag.WORKFLOW_ATTEMPT.value: "1",
        SentryTag.WORKFLOW_TYPE.value: "DSLWorkflow",
        SentryTag.ERROR_CAUSE_TYPE.value: "RuntimeError",
        SentryTag.ERROR_KIND.value: RuntimeErrorKind.RUNTIME_UNCLASSIFIED.value,
        SentryTag.ERROR_OWNER.value: "platform",
        SentryTag.ERROR_RETRY_DISPOSITION.value: "non_retryable",
        SentryTag.TRIGGER_TYPE.value: "manual",
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
    attribution = _RuntimeErrorAttributionWorkflowInterceptor(_RaisingInbound(error))

    with pytest.raises(ApplicationError):
        await attribution.execute_workflow(_workflow_input())
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
        "capture_platform_failure",
        lambda *_: (_ for _ in ()).throw(RuntimeError("capture unavailable")),
    )
    attribution = _RuntimeErrorAttributionWorkflowInterceptor(_RaisingInbound(error))

    with pytest.raises(ApplicationError) as raised:
        await attribution.execute_workflow(_workflow_input())

    assert raised.value is error
    assert sentry_events == []


def test_error_logs_are_not_promoted_to_sentry_events(
    sentry_events: list[Event],
) -> None:
    logger.error("Synthetic log error", value=_SENSITIVE_VALUE)
    sentry_sdk.flush()

    assert sentry_events == []
