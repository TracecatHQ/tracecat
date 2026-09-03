"""Declarative acceptance matrix for durable-agent failure attribution."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment

from tests.temporal import durable_agent_failure_harness as harness
from tracecat.agent.error_policy import (
    agent_executor_protocol_failed,
    tenant_entitlement_denied,
    user_agent_execution_failed,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import extract_error_classifications
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal]

# Re-export the harness fixture under the name collected by this test module.
temporal_env = harness.env
agent_worker_factory = harness.worker_factory

_FAILED = WorkflowExecutionStatus.FAILED
_DIAGNOSTIC = "synthetic sensitive diagnostic must not reach the terminal envelope"


@dataclass(frozen=True, slots=True)
class _FailureScenario:
    """One row in the complete durable-agent failure acceptance matrix."""

    id: str
    fault: str
    injection: harness.FailureInjection
    status: WorkflowExecutionStatus
    owner: RuntimeErrorOwner
    kind: RuntimeErrorKind
    retry_disposition: RetryDisposition
    should_stream: bool
    fault_calls: int = 1
    emitted_error_count: int = 1
    emitted_error_failure_count: int = 0
    finalized_turn_count: int = 1
    diagnostic_absent_from_history: bool = False


_WORKFLOW_FAILURE_SCENARIOS: tuple[_FailureScenario, ...] = (
    _FailureScenario(
        id="initialization.workspace_context_missing",
        fault="workflow role has no workspace context",
        injection=harness.FailureInjection(
            harness.FaultPoint.WORKSPACE_CONTEXT_MISSING
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.USER,
        kind=RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=False,
        fault_calls=0,
        emitted_error_count=0,
        finalized_turn_count=0,
    ),
    _FailureScenario(
        id="initialization.organization_context_missing",
        fault="workflow role has no organization context",
        injection=harness.FailureInjection(
            harness.FaultPoint.ORGANIZATION_CONTEXT_MISSING
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.USER,
        kind=RuntimeErrorKind.AGENT_CONFIGURATION_INVALID,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=False,
        fault_calls=0,
        emitted_error_count=0,
        finalized_turn_count=0,
    ),
    _FailureScenario(
        id="tool_definitions.classified_entitlement",
        fault="tool-definition activity raises a typed entitlement denial",
        injection=harness.FailureInjection(
            harness.FaultPoint.TOOL_DEFINITIONS_ACTIVITY,
            classification=tenant_entitlement_denied(ValueError(_DIAGNOSTIC)),
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.USER,
        kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
        diagnostic_absent_from_history=True,
    ),
    _FailureScenario(
        id="preparation.activity_crash",
        fault="tool-definition activity raises an unclassified exception",
        injection=harness.FailureInjection(
            harness.FaultPoint.TOOL_DEFINITIONS_ACTIVITY
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_PREPARATION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="preparation.activity_non_retryable",
        fault="tool-definition activity raises an unclassified non-retryable error",
        injection=harness.FailureInjection(
            harness.FaultPoint.TOOL_DEFINITIONS_ACTIVITY,
            activity_non_retryable=True,
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_PREPARATION_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="preparation.tool_definitions_missing_root",
        fault="tool-definition activity omits the required root scope",
        injection=harness.FailureInjection(harness.FaultPoint.TOOL_DEFINITIONS_RESULT),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_PREPARATION_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="session.initialization_result",
        fault="session creation returns an unsuccessful result",
        injection=harness.FailureInjection(harness.FaultPoint.SESSION_RESULT),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_SESSION_INITIALIZATION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="executor.activity_crash",
        fault="executor activity raises an unclassified exception",
        injection=harness.FailureInjection(harness.FaultPoint.EXECUTOR_ACTIVITY),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE,
        retry_disposition=RetryDisposition.RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="executor.activity_classified",
        fault="executor activity raises an explicitly classified failure",
        injection=harness.FailureInjection(
            harness.FaultPoint.EXECUTOR_ACTIVITY,
            classification=user_agent_execution_failed(
                ValueError(_DIAGNOSTIC), retryable=True
            ),
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.USER,
        kind=RuntimeErrorKind.AGENT_EXECUTION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        should_stream=True,
        diagnostic_absent_from_history=True,
    ),
    _FailureScenario(
        id="executor.activity_timeout",
        fault="executor activity exceeds its start-to-close timeout",
        injection=harness.FailureInjection(harness.FaultPoint.EXECUTOR_TIMEOUT),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT,
        retry_disposition=RetryDisposition.RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="executor.result_user_retryable",
        fault="executor returns a typed retryable caller/provider failure",
        injection=harness.FailureInjection(
            harness.FaultPoint.EXECUTOR_RESULT,
            classification=user_agent_execution_failed(
                ValueError(_DIAGNOSTIC), retryable=True
            ),
            terminal_stream_error_emitted=True,
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.USER,
        kind=RuntimeErrorKind.AGENT_EXECUTION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        should_stream=False,
    ),
    _FailureScenario(
        id="executor.result_protocol",
        fault="executor returns a typed non-retryable protocol failure",
        injection=harness.FailureInjection(
            harness.FaultPoint.EXECUTOR_RESULT,
            classification=agent_executor_protocol_failed(ValueError(_DIAGNOSTIC)),
            terminal_stream_error_emitted=False,
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="streaming.error_persistence_activity_crash",
        fault="error persistence/streaming fails without masking the root failure",
        injection=harness.FailureInjection(
            harness.FaultPoint.EXECUTOR_RESULT,
            classification=agent_executor_protocol_failed(ValueError(_DIAGNOSTIC)),
            terminal_stream_error_emitted=False,
            emit_session_error_fails=True,
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_EXECUTOR_PROTOCOL_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
        emitted_error_failure_count=1,
    ),
    _FailureScenario(
        id="executor.result_missing_classification",
        fault="executor violates its contract by omitting a failure classification",
        injection=harness.FailureInjection(
            harness.FaultPoint.EXECUTOR_RESULT,
            terminal_stream_error_emitted=False,
        ),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_WORKFLOW_INTERNAL_ERROR,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
    ),
    _FailureScenario(
        id="workflow.internal_exception",
        fault="workflow-owned token preparation raises an unexpected exception",
        injection=harness.FailureInjection(harness.FaultPoint.WORKFLOW_INTERNAL),
        status=_FAILED,
        owner=RuntimeErrorOwner.PLATFORM,
        kind=RuntimeErrorKind.AGENT_WORKFLOW_INTERNAL_ERROR,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        should_stream=True,
        diagnostic_absent_from_history=True,
    ),
)


@dataclass(frozen=True, slots=True)
class _GatewayExpectation:
    """One source-level gateway failure and its expected durable attribution."""

    route: harness.GatewayRoute
    mode: harness.GatewayFailureMode
    owner: RuntimeErrorOwner
    kind: RuntimeErrorKind
    retry_disposition: RetryDisposition


def _gateway_scenario(expectation: _GatewayExpectation) -> _FailureScenario:
    return _FailureScenario(
        id=f"gateway.{expectation.route.value}.{expectation.mode.value}",
        fault=(
            f"{expectation.route.value} produces "
            f"{expectation.mode.value.replace('_', ' ')}"
        ),
        injection=harness.FailureInjection(
            harness.FaultPoint.EXECUTOR_RESULT,
            gateway_failure=harness.GatewayFailureInjection(
                route=expectation.route,
                mode=expectation.mode,
            ),
            # Proxy failures are terminal-streamed by AgentExecutor before its
            # typed result crosses the Temporal activity boundary.
            terminal_stream_error_emitted=True,
        ),
        status=_FAILED,
        owner=expectation.owner,
        kind=expectation.kind,
        retry_disposition=expectation.retry_disposition,
        should_stream=False,
    )


_USER = RuntimeErrorOwner.USER
_PLATFORM = RuntimeErrorOwner.PLATFORM
_EXECUTION_FAILED = RuntimeErrorKind.AGENT_EXECUTION_FAILED
_UNAVAILABLE = RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
_TIMED_OUT = RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT
_RETRYABLE = RetryDisposition.RETRYABLE
_NON_RETRYABLE = RetryDisposition.NON_RETRYABLE

# A custom gateway is currently a customer-controlled direct route with a custom
# base URL, so it intentionally shares ownership semantics with direct providers.
_GATEWAY_EXPECTATIONS: tuple[_GatewayExpectation, ...] = (
    # Direct provider (no gateway).
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.HTTP_400,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.HTTP_401,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.HTTP_429,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.HTTP_503,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.HTTP_504,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.CONNECT,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.READ_TIMEOUT,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.DIRECT_PROVIDER,
        harness.GatewayFailureMode.STREAM_DISCONNECT,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    # Customer-configured gateway (implemented as a direct custom base URL).
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.HTTP_400,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.HTTP_401,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.HTTP_429,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.HTTP_503,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.HTTP_504,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.CONNECT,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.READ_TIMEOUT,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.CUSTOM_GATEWAY,
        harness.GatewayFailureMode.STREAM_DISCONNECT,
        _USER,
        _EXECUTION_FAILED,
        _RETRYABLE,
    ),
    # Tracecat-managed LiteLLM.
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.HTTP_400,
        _USER,
        _EXECUTION_FAILED,
        _NON_RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.HTTP_401,
        _PLATFORM,
        _UNAVAILABLE,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.HTTP_429,
        _PLATFORM,
        _UNAVAILABLE,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.HTTP_503,
        _PLATFORM,
        _UNAVAILABLE,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.HTTP_504,
        _PLATFORM,
        _TIMED_OUT,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.CONNECT,
        _PLATFORM,
        _UNAVAILABLE,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.READ_TIMEOUT,
        _PLATFORM,
        _TIMED_OUT,
        _RETRYABLE,
    ),
    _GatewayExpectation(
        harness.GatewayRoute.MANAGED_LITELLM,
        harness.GatewayFailureMode.STREAM_DISCONNECT,
        _PLATFORM,
        _UNAVAILABLE,
        _RETRYABLE,
    ),
)


FAILURE_SCENARIOS: tuple[_FailureScenario, ...] = (
    *_WORKFLOW_FAILURE_SCENARIOS,
    *(_gateway_scenario(expectation) for expectation in _GATEWAY_EXPECTATIONS),
)


def _scenario_id(scenario: _FailureScenario) -> str:
    return scenario.id


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", FAILURE_SCENARIOS, ids=_scenario_id)
async def test_durable_agent_failure_attribution(
    scenario: _FailureScenario,
    temporal_env: WorkflowEnvironment,
    agent_worker_factory: harness.WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run every durable-agent failure contract row through a real Worker."""
    observation = await harness.run_failure_scenario(
        temporal_env,
        agent_worker_factory,
        monkeypatch,
        injection=scenario.injection,
        diagnostic=_DIAGNOSTIC,
    )

    description = await observation.root.describe()
    assert description.status is scenario.status
    assert (
        description.typed_search_attributes.get(TemporalSearchAttr.ERROR_OWNER.key)
        == scenario.owner.value
    )

    classifications = extract_error_classifications(observation.failure)
    assert len(classifications) == 1
    classification = classifications[0]
    assert classification.owner is scenario.owner
    assert classification.kind is scenario.kind
    assert classification.retry_disposition is scenario.retry_disposition
    assert _DIAGNOSTIC not in classification.message

    assert isinstance(observation.failure.cause, ApplicationError)
    assert observation.failure.cause.non_retryable is (
        scenario.retry_disposition is RetryDisposition.NON_RETRYABLE
    )
    assert _DIAGNOSTIC not in str(observation.failure.cause)
    assert observation.fault_calls == scenario.fault_calls

    assert len(observation.emitted_errors) == scenario.emitted_error_count
    assert observation.emit_error_failures == scenario.emitted_error_failure_count
    for emitted_error in observation.emitted_errors:
        assert emitted_error.message == classification.message
        assert emitted_error.should_stream is scenario.should_stream
        assert _DIAGNOSTIC not in emitted_error.message

    assert len(observation.finalized_turns) == scenario.finalized_turn_count
    for finalized_turn in observation.finalized_turns:
        assert finalized_turn.emit_terminal_done is True
    assert observation.emitted_done == ()

    if scenario.diagnostic_absent_from_history:
        assert _DIAGNOSTIC not in observation.history.to_json()

    await harness.replay_scenario_history(temporal_env, observation.history)
