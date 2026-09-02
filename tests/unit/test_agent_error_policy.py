from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import cast

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    RetryState,
    TimeoutError,
    TimeoutType,
)
from tracecat_ee.agent.workflows.durable import (
    _agent_activity_classification,
    _executor_activity_classification,
)

from tracecat.agent.error_policy import (
    agent_executor_protocol_failed,
    agent_executor_timed_out,
    agent_executor_unavailable,
    agent_preparation_failed,
    agent_session_initialization_failed,
    agent_workflow_internal_error,
    invalid_agent_configuration,
    user_agent_execution_failed,
)
from tracecat.agent.executor.activity import AgentExecutorResult, SandboxedAgentExecutor
from tracecat.agent.executor.loopback import (
    LoopbackEventSink,
    LoopbackHandler,
    LoopbackInput,
    LoopbackResult,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import application_error_from_classification


def _activity_error(cause: BaseException) -> ActivityError:
    error = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test-worker",
        activity_type="test_activity",
        activity_id="activity-id",
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    error.__cause__ = cause
    return error


@pytest.mark.parametrize(
    ("factory", "owner", "kind", "retry_disposition"),
    [
        (
            invalid_agent_configuration,
            RuntimeErrorOwner.USER,
            RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            lambda error: agent_preparation_failed(error, retryable=True),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_PREPARATION_FAILED,
            RetryDisposition.RETRYABLE,
        ),
        (
            lambda error: agent_session_initialization_failed(error, retryable=False),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_SESSION_INITIALIZATION_FAILED,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            user_agent_execution_failed,
            RuntimeErrorOwner.USER,
            RuntimeErrorKind.AGENT_EXECUTION_FAILED,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            agent_executor_unavailable,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
            RetryDisposition.RETRYABLE,
        ),
        (
            agent_executor_timed_out,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT,
            RetryDisposition.RETRYABLE,
        ),
        (
            agent_executor_protocol_failed,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            agent_workflow_internal_error,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.AGENT_WORKFLOW_INTERNAL_ERROR,
            RetryDisposition.NON_RETRYABLE,
        ),
    ],
)
def test_agent_classification_factories_are_static_and_sanitized(
    factory: Callable[[BaseException | None], RuntimeErrorClassification],
    owner: RuntimeErrorOwner,
    kind: RuntimeErrorKind,
    retry_disposition: RetryDisposition,
) -> None:
    secret = "raw prompt and token"
    classification = factory(ValueError(secret))

    assert classification.owner is owner
    assert classification.kind is kind
    assert classification.retry_disposition is retry_disposition
    assert classification.cause_type == "ValueError"
    assert secret not in classification.message


def test_agent_executor_result_round_trips_typed_classification() -> None:
    classification = user_agent_execution_failed()

    result = AgentExecutorResult(success=False, classification=classification)
    restored = AgentExecutorResult.model_validate(result.model_dump(mode="json"))

    assert restored.classification == classification


def test_agent_executor_result_accepts_legacy_payload_without_classification() -> None:
    result = AgentExecutorResult.model_validate(
        {"success": False, "error": "legacy free-form failure"}
    )

    assert result.classification is None


def test_agent_activity_does_not_infer_user_owner_from_non_retryable() -> None:
    error = _activity_error(ApplicationError("invalid", non_retryable=True))

    classification = _agent_activity_classification(error)

    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE


def test_agent_activity_classifies_untyped_crash_as_platform_preparation() -> None:
    classification = _agent_activity_classification(_activity_error(RuntimeError()))

    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


def test_agent_activity_preserves_existing_classification() -> None:
    expected = invalid_agent_configuration()
    error = _activity_error(application_error_from_classification(expected))

    assert _agent_activity_classification(error) == expected


def test_agent_activity_ignores_incidental_classified_context() -> None:
    error = _activity_error(RuntimeError("current platform failure"))
    error.__context__ = application_error_from_classification(
        invalid_agent_configuration()
    )

    classification = _agent_activity_classification(error)

    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED


def test_executor_activity_classifies_timeout_without_message_inspection() -> None:
    timeout = TimeoutError(
        "opaque timeout",
        type=TimeoutType.START_TO_CLOSE,
        last_heartbeat_details=[],
    )

    classification = _executor_activity_classification(_activity_error(timeout))

    assert classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


def test_executor_activity_classifies_crash_as_unavailable() -> None:
    classification = _executor_activity_classification(
        _activity_error(RuntimeError("opaque crash"))
    )

    assert classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


class _StreamSink:
    def __init__(self) -> None:
        self.errors: list[str] = []

    async def error(self, error: str) -> None:
        self.errors.append(error)


@pytest.mark.anyio
async def test_runtime_reported_error_is_user_classified() -> None:
    handler = LoopbackHandler(
        input=LoopbackInput(
            session_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        )
    )
    sink = _StreamSink()
    handler._stream_sink = cast(LoopbackEventSink, sink)

    await handler.send_error("raw provider response")
    result = handler.build_result()

    assert result.classification is not None
    assert result.classification.owner is RuntimeErrorOwner.USER
    assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTION_FAILED
    assert "raw provider response" not in result.classification.message


def test_loopback_classification_is_copied_to_executor_result() -> None:
    classification = agent_executor_protocol_failed()
    result = AgentExecutorResult(success=False)

    SandboxedAgentExecutor._apply_loopback_result(
        result,
        LoopbackResult(success=False, classification=classification),
    )

    assert result.classification == classification
