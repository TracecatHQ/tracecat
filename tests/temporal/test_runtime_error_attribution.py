"""Declarative coverage matrix for runtime error attribution semantics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle
from temporalio.testing import WorkflowEnvironment

from tests.temporal import runtime_error_attribution_harness as harness
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLeaseContentionError,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.sandbox.exceptions import SandboxInfrastructureError
from tracecat.temporal.errors import (
    extract_error_classification,
    extract_error_classifications,
)
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal]

# Re-export the harness fixtures under the names collected by this test module.
disable_workflow_concurrency_limits = harness.disable_workflow_concurrency_limits
temporal_env = harness.env


class _Topology(StrEnum):
    SINGLE_ACTION = "single_action"
    DEFINITION_LOOKUP = "definition_lookup"
    SCATTER_GATHER = "scatter_gather"
    FANOUT = "fanout"
    ERROR_HANDLER = "error_handler"
    AUTHORED_ERROR_EDGE = "authored_error_edge"
    ENGINE_TERMINAL = "engine_terminal"


class _FaultPoint(StrEnum):
    MATERIALIZATION = "retrieve_stored_object"
    EXECUTOR_BACKEND = "get_executor_backend"
    EXECUTOR_DISPATCH = "dispatch_action"
    RESULT_PERSISTENCE = "object_storage.store"
    DEFINITION_SERVICE = "get_definition_by_workflow_id"
    CHILD_EXECUTION = "child_dispatch"
    SUBFLOW_PREPARATION = "prepare_subflow"
    TEMPORAL_CONTROL = "temporal_control"


type _TerminalOperation = Literal["cancel", "terminate", "timeout"]


@dataclass(frozen=True, slots=True)
class _ExecutionExpectation:
    status: WorkflowExecutionStatus
    owner: RuntimeErrorOwner | None


@dataclass(frozen=True, slots=True)
class _AttributionScenario:
    """One row in the complete runtime-attribution acceptance matrix."""

    id: str
    topology: _Topology
    fault_point: _FaultPoint
    fault: str
    root: _ExecutionExpectation
    runner: Callable[[_ScenarioContext], Awaitable[harness.ScenarioObservation]] = (
        field(repr=False)
    )
    children: tuple[_ExecutionExpectation, ...] = ()
    envelope_owners: frozenset[RuntimeErrorOwner] = frozenset()
    kind: RuntimeErrorKind | None = None
    retry_disposition: RetryDisposition | None = None
    attempts: int | None = None


@dataclass(frozen=True, slots=True)
class _ScenarioContext:
    env: WorkflowEnvironment
    test_worker_factory: Any
    monkeypatch: pytest.MonkeyPatch


type _BasicRunner = Callable[
    [WorkflowEnvironment, Any], Awaitable[harness.ScenarioObservation]
]
type _MonkeypatchRunner = Callable[
    [WorkflowEnvironment, Any, pytest.MonkeyPatch],
    Awaitable[harness.ScenarioObservation],
]


def _basic_runner(
    run: _BasicRunner,
) -> Callable[[_ScenarioContext], Awaitable[harness.ScenarioObservation]]:
    async def wrapped(context: _ScenarioContext) -> harness.ScenarioObservation:
        return await run(context.env, context.test_worker_factory)

    return wrapped


def _monkeypatch_runner(
    run: _MonkeypatchRunner,
) -> Callable[[_ScenarioContext], Awaitable[harness.ScenarioObservation]]:
    async def wrapped(context: _ScenarioContext) -> harness.ScenarioObservation:
        return await run(
            context.env,
            context.test_worker_factory,
            context.monkeypatch,
        )

    return wrapped


def _executor_runner(
    *,
    error_factory: Callable[[], Exception],
    max_attempts: int,
) -> Callable[[_ScenarioContext], Awaitable[harness.ScenarioObservation]]:
    async def wrapped(context: _ScenarioContext) -> harness.ScenarioObservation:
        return await harness.run_executor_internal_failure_sets_platform_owner(
            context.env,
            context.test_worker_factory,
            error_factory,
            max_attempts,
        )

    return wrapped


def _executor_boundary_runner(
    fault_point: harness.ExecutorBoundaryFaultPoint,
) -> Callable[[_ScenarioContext], Awaitable[harness.ScenarioObservation]]:
    async def wrapped(context: _ScenarioContext) -> harness.ScenarioObservation:
        return await harness.run_executor_boundary_failure_sets_owner(
            context.env,
            context.test_worker_factory,
            fault_point,
        )

    return wrapped


def _engine_runner(
    operation: _TerminalOperation,
) -> Callable[[_ScenarioContext], Awaitable[harness.ScenarioObservation]]:
    async def wrapped(context: _ScenarioContext) -> harness.ScenarioObservation:
        return await harness.run_engine_terminal_status_does_not_set_error_owner(
            context.env,
            context.test_worker_factory,
            operation,
        )

    return wrapped


_COMPLETED = WorkflowExecutionStatus.COMPLETED
_FAILED = WorkflowExecutionStatus.FAILED
_CANCELED = WorkflowExecutionStatus.CANCELED
_TERMINATED = WorkflowExecutionStatus.TERMINATED
_TIMED_OUT = WorkflowExecutionStatus.TIMED_OUT


ATTRIBUTION_SCENARIOS: tuple[_AttributionScenario, ...] = (
    _AttributionScenario(
        id="materialization.transport.exhausted",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.MATERIALIZATION,
        fault="HTTPClientError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_TRANSPORT_UNAVAILABLE,
        retry_disposition=RetryDisposition.RETRYABLE,
        attempts=2,
        runner=_monkeypatch_runner(
            harness.run_retryable_materialization_failure_sets_platform_owner
        ),
    ),
    _AttributionScenario(
        id="materialization.integrity.non_retryable",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.MATERIALIZATION,
        fault="ValueError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.STORAGE_MATERIALIZATION_INVALID_DATA,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_monkeypatch_runner(
            harness.run_non_retryable_materialization_failure_does_not_retry
        ),
    ),
    _AttributionScenario(
        id="materialization.transport.recovered",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.MATERIALIZATION,
        fault="HTTPClientError",
        root=_ExecutionExpectation(_COMPLETED, None),
        attempts=3,
        runner=_monkeypatch_runner(
            harness.run_successful_materialization_retry_does_not_set_terminal_owner
        ),
    ),
    _AttributionScenario(
        id="executor.backend_initialization.non_retryable",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_BACKEND,
        fault="RuntimeError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.EXECUTOR_BACKEND_INITIALIZATION_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_executor_boundary_runner("backend_initialization"),
    ),
    _AttributionScenario(
        id="executor.result_persistence.non_retryable",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.RESULT_PERSISTENCE,
        fault="HTTPClientError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.STORAGE_PERSISTENCE_TRANSPORT_UNAVAILABLE,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_executor_boundary_runner("result_persistence"),
    ),
    _AttributionScenario(
        id="executor.loop.platform_origin",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="LoopExecutionError(RegistryArtifactExtractionError)",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.EXECUTOR_REGISTRY_EXTRACTION_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_executor_boundary_runner("loop_platform"),
    ),
    _AttributionScenario(
        id="executor.entitlement.user",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="EntitlementRequired",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.USER),
        envelope_owners=frozenset({RuntimeErrorOwner.USER}),
        kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_executor_boundary_runner("entitlement"),
    ),
    _AttributionScenario(
        id="executor.registry_lease.exhausted",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="RegistryArtifactCacheLeaseContentionError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.EXECUTOR_REGISTRY_LEASE_CONTENTION,
        retry_disposition=RetryDisposition.RETRYABLE,
        attempts=2,
        runner=_executor_runner(
            error_factory=lambda: RegistryArtifactCacheLeaseContentionError(
                current_bytes=80,
                additional_bytes=30,
                max_bytes=100,
            ),
            max_attempts=2,
        ),
    ),
    _AttributionScenario(
        id="executor.registry_capacity.non_retryable",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="RegistryArtifactCacheCapacityError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.EXECUTOR_REGISTRY_CAPACITY_EXHAUSTED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_executor_runner(
            error_factory=lambda: RegistryArtifactCacheCapacityError(
                current_bytes=80,
                additional_bytes=30,
                max_bytes=100,
            ),
            max_attempts=3,
        ),
    ),
    _AttributionScenario(
        id="executor.sandbox.exhausted",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="SandboxInfrastructureError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.EXECUTOR_SANDBOX_INFRASTRUCTURE_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        attempts=2,
        runner=_executor_runner(
            error_factory=lambda: SandboxInfrastructureError(
                "sandbox supervisor diagnostic must not enter history"
            ),
            max_attempts=2,
        ),
    ),
    _AttributionScenario(
        id="executor.registry_lease.recovered",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="RegistryArtifactCacheLeaseContentionError",
        root=_ExecutionExpectation(_COMPLETED, None),
        attempts=3,
        runner=_basic_runner(
            harness.run_successful_registry_contention_retry_has_no_terminal_owner
        ),
    ),
    _AttributionScenario(
        id="executor.user_action.exhausted",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.EXECUTOR_DISPATCH,
        fault="ValueError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.USER),
        envelope_owners=frozenset({RuntimeErrorOwner.USER}),
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        attempts=2,
        runner=_basic_runner(harness.run_user_action_failure_sets_user_owner),
    ),
    _AttributionScenario(
        id="definition.missing_published_version",
        topology=_Topology.DEFINITION_LOOKUP,
        fault_point=_FaultPoint.DEFINITION_SERVICE,
        fault="missing definition",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.WORKFLOW_DEFINITION_NOT_FOUND,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_monkeypatch_runner(
            harness.run_missing_published_definition_sets_platform_owner
        ),
    ),
    _AttributionScenario(
        id="definition.lookup_unavailable",
        topology=_Topology.DEFINITION_LOOKUP,
        fault_point=_FaultPoint.DEFINITION_SERVICE,
        fault="RuntimeError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.WORKFLOW_DEFINITION_LOOKUP_UNAVAILABLE,
        retry_disposition=RetryDisposition.RETRYABLE,
        attempts=6,
        runner=_monkeypatch_runner(
            harness.run_definition_lookup_failure_sets_platform_owner
        ),
    ),
    _AttributionScenario(
        id="fanout.isolated.platform_child",
        topology=_Topology.FANOUT,
        fault_point=_FaultPoint.CHILD_EXECUTION,
        fault="platform child + successful child",
        root=_ExecutionExpectation(_COMPLETED, None),
        children=(
            _ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
            _ExecutionExpectation(_COMPLETED, None),
        ),
        runner=_basic_runner(
            harness.run_isolated_platform_child_failure_does_not_attribute_parent
        ),
    ),
    _AttributionScenario(
        id="fanout.fail_all.mixed_children",
        topology=_Topology.FANOUT,
        fault_point=_FaultPoint.CHILD_EXECUTION,
        fault="user child + platform child + successful child",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        children=(
            _ExecutionExpectation(_FAILED, RuntimeErrorOwner.USER),
            _ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
            _ExecutionExpectation(_COMPLETED, None),
        ),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        runner=_basic_runner(harness.run_fail_all_preserves_mixed_child_attribution),
    ),
    _AttributionScenario(
        id="error_handler.successful_child",
        topology=_Topology.ERROR_HANDLER,
        fault_point=_FaultPoint.CHILD_EXECUTION,
        fault="user parent failure + successful handler",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.USER),
        children=(_ExecutionExpectation(_COMPLETED, None),),
        envelope_owners=frozenset({RuntimeErrorOwner.USER}),
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        runner=_monkeypatch_runner(
            harness.run_successful_error_handler_does_not_inherit_terminal_owner
        ),
    ),
    _AttributionScenario(
        id="subflow_preparation.unhandled_platform_failure",
        topology=_Topology.SINGLE_ACTION,
        fault_point=_FaultPoint.SUBFLOW_PREPARATION,
        fault="RuntimeError",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.WORKFLOW_SUBFLOW_PREPARATION_FAILED,
        retry_disposition=RetryDisposition.RETRYABLE,
        attempts=1,
        runner=_basic_runner(
            harness.run_unhandled_subflow_preparation_failure_sets_platform_owner
        ),
    ),
    _AttributionScenario(
        id="gather.raise.preserves_platform_child",
        topology=_Topology.SCATTER_GATHER,
        fault_point=_FaultPoint.SUBFLOW_PREPARATION,
        fault="RuntimeError in scattered subflow preparation",
        root=_ExecutionExpectation(_FAILED, RuntimeErrorOwner.PLATFORM),
        envelope_owners=frozenset({RuntimeErrorOwner.PLATFORM}),
        kind=RuntimeErrorKind.ACTION_EXECUTION_FAILED,
        retry_disposition=RetryDisposition.NON_RETRYABLE,
        attempts=1,
        runner=_basic_runner(
            harness.run_gather_raise_preserves_platform_child_attribution
        ),
    ),
    _AttributionScenario(
        id="error_edge.handled_platform_failure",
        topology=_Topology.AUTHORED_ERROR_EDGE,
        fault_point=_FaultPoint.SUBFLOW_PREPARATION,
        fault="platform preparation failure",
        root=_ExecutionExpectation(_COMPLETED, None),
        runner=_basic_runner(
            harness.run_handled_platform_activity_failure_uses_authored_error_edge
        ),
    ),
    _AttributionScenario(
        id="engine.cancel",
        topology=_Topology.ENGINE_TERMINAL,
        fault_point=_FaultPoint.TEMPORAL_CONTROL,
        fault="cancel",
        root=_ExecutionExpectation(_CANCELED, None),
        runner=_engine_runner("cancel"),
    ),
    _AttributionScenario(
        id="engine.terminate",
        topology=_Topology.ENGINE_TERMINAL,
        fault_point=_FaultPoint.TEMPORAL_CONTROL,
        fault="terminate",
        root=_ExecutionExpectation(_TERMINATED, None),
        runner=_engine_runner("terminate"),
    ),
    _AttributionScenario(
        id="engine.timeout",
        topology=_Topology.ENGINE_TERMINAL,
        fault_point=_FaultPoint.TEMPORAL_CONTROL,
        fault="timeout",
        root=_ExecutionExpectation(_TIMED_OUT, None),
        runner=_engine_runner("timeout"),
    ),
)


def _scenario_id(scenario: _AttributionScenario) -> str:
    return scenario.id


async def _execution_expectation(
    handle: WorkflowHandle[Any, Any],
) -> _ExecutionExpectation:
    description = await handle.describe()
    assert description.status is not None
    owner_value = description.typed_search_attributes.get(
        TemporalSearchAttr.ERROR_OWNER.key
    )
    owner = RuntimeErrorOwner(owner_value) if owner_value is not None else None
    return _ExecutionExpectation(status=description.status, owner=owner)


async def _assert_scenario_observation(
    scenario: _AttributionScenario,
    observation: harness.ScenarioObservation,
) -> None:
    assert await _execution_expectation(observation.root) == scenario.root

    expected_failure = scenario.root.status != _COMPLETED
    assert (observation.failure is not None) is expected_failure

    observed_children = [
        await _execution_expectation(child) for child in observation.children
    ]
    assert Counter(observed_children) == Counter(scenario.children)

    if observation.failure is not None:
        classifications = extract_error_classifications(observation.failure)
        assert {classification.owner for classification in classifications} == set(
            scenario.envelope_owners
        )
        if scenario.kind is not None or scenario.retry_disposition is not None:
            classification = extract_error_classification(observation.failure)
            assert classification is not None
            assert classification.kind is scenario.kind
            assert classification.retry_disposition is scenario.retry_disposition
    else:
        assert not scenario.envelope_owners
        assert scenario.kind is None
        assert scenario.retry_disposition is None

    assert observation.attempts == scenario.attempts


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", ATTRIBUTION_SCENARIOS, ids=_scenario_id)
async def test_runtime_error_attribution(
    scenario: _AttributionScenario,
    temporal_env: WorkflowEnvironment,
    test_worker_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run every attribution contract row through its real Temporal topology."""
    context = _ScenarioContext(
        env=temporal_env,
        test_worker_factory=test_worker_factory,
        monkeypatch=monkeypatch,
    )
    observation = await scenario.runner(context)
    await _assert_scenario_observation(scenario, observation)
