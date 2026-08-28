"""Real-Worker topology runners for the runtime attribution scenario matrix."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any, Literal
from unittest.mock import AsyncMock, patch

import pytest
from botocore.exceptions import HTTPClientError
from temporalio.api.enums.v1 import EventType
from temporalio.client import (
    WorkflowFailureError,
    WorkflowHandle,
)
from temporalio.testing import WorkflowEnvironment

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl import action as action_module
from tracecat.dsl import init_activities as init_activities_module
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    DSLRunArgs,
    PreparedSubflowResult,
)
from tracecat.dsl.enums import (
    FailStrategy,
    PlatformAction,
    WaitStrategy,
)
from tracecat.dsl.init_activities import (
    resolve_time_anchor_activity,
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.schemas import (
    ActionRetryPolicy,
    ActionStatement,
    DSLConfig,
    RunActionInput,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.exceptions import EntitlementRequired, ExecutionError, LoopExecutionError
from tracecat.executor.activities import ExecutorActivities
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheLeaseContentionError,
    RegistryArtifactExtractionError,
)
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import ExternalObject, InlineObject, ObjectRef
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = [pytest.mark.temporal]

type ExecutorBoundaryFaultPoint = Literal[
    "backend_initialization",
    "result_persistence",
    "loop_platform",
    "entitlement",
]


@pytest.fixture(autouse=True)
def disable_workflow_concurrency_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the minimal Worker focused on error attribution."""
    monkeypatch.setattr(
        init_activities_module,
        "is_feature_enabled",
        lambda _feature: False,
    )


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Run attribution tests against an ephemeral Temporal dev server."""
    async with await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(compression_enabled=False),
        search_attributes=[TemporalSearchAttr.ERROR_OWNER.key],
        dev_server_log_level="error",
    ) as environment:
        yield environment


@dataclass(slots=True)
class _MaterializationFault:
    """Deterministically fail StoredObject retrieval before eventually succeeding."""

    error_factory: Callable[[], Exception]
    failures: int
    attempts: int = 0

    async def retrieve(self, _stored: object) -> object:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error_factory()
        return {"source": "stored-trigger"}


@dataclass(slots=True)
class _DispatchFault:
    """Inject a typed dependency failure behind the real executor activity."""

    error_factory: Callable[[], Exception]
    failures: int
    attempts: int = 0

    async def dispatch(self, *, backend: object, input: object) -> object:
        del backend, input
        self.attempts += 1
        if self.attempts <= self.failures:
            cause = self.error_factory()
            info = ExecutorActionErrorInfo(
                type=type(cause).__name__,
                message="masked executor failure",
                action_name="core.noop",
                filename="<executor>",
                function="dispatch",
            )
            raise ExecutionError(info=info) from cause
        return {"ok": True}


@dataclass(slots=True)
class _ChildDispatch:
    """Fail child actions by trigger mode while allowing parent recovery work."""

    user_diagnostic: str
    platform_diagnostic: str
    calls: list[str]

    async def dispatch(self, *, backend: object, input: RunActionInput) -> object:
        del backend
        trigger = input.exec_context.get("TRIGGER")
        mode = trigger.get("mode") if isinstance(trigger, dict) else None
        self.calls.append(mode if isinstance(mode, str) else "success")
        if mode not in {"platform", "user"}:
            return {"ok": True}

        if mode == "platform":
            cause = RuntimeError(self.platform_diagnostic)
            cause.__cause__ = RegistryArtifactExtractionError()
        else:
            cause = ValueError(self.user_diagnostic)

        info = ExecutorActionErrorInfo(
            type=type(cause).__name__,
            message="masked child action failure",
            action_name="core.noop",
            filename="<executor>",
            function="dispatch",
        )
        raise ExecutionError(info=info) from cause


@dataclass(frozen=True, slots=True)
class ScenarioObservation:
    """Execution handles and outcomes consumed by the shared matrix assertion."""

    root: WorkflowHandle[Any, Any]
    failure: WorkflowFailureError | None = None
    children: tuple[WorkflowHandle[Any, Any], ...] = ()
    attempts: int | None = None


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )


def _external_trigger() -> ExternalObject:
    return ExternalObject(
        ref=ObjectRef(
            bucket="workflow-data",
            key="synthetic/trigger.json",
            size_bytes=128,
            sha256="0" * 64,
        )
    )


def _run_args(*, max_attempts: int) -> DSLRunArgs:
    action_name = "core.noop"
    return DSLRunArgs(
        role=_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Runtime error attribution probe",
            description="Exercise activity-origin error attribution",
            entrypoint=DSLEntrypoint(ref="run"),
            actions=[
                ActionStatement(
                    ref="run",
                    action=action_name,
                    args={},
                    retry_policy=ActionRetryPolicy(
                        max_attempts=max_attempts,
                        timeout=10,
                    ),
                )
            ],
        ),
        trigger_inputs=_external_trigger(),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={action_name: "tracecat_registry"},
        ),
    )


def _inline_run_args(*, max_attempts: int) -> DSLRunArgs:
    return _run_args(max_attempts=max_attempts).model_copy(
        update={"trigger_inputs": InlineObject(data={"source": "inline-trigger"})}
    )


def _transport_error(secret: str) -> HTTPClientError:
    return HTTPClientError(error=RuntimeError(secret))


async def describe_status(handle: WorkflowHandle[Any, Any]) -> int:
    status = (await handle.describe()).status
    assert status is not None
    return int(status)


async def child_workflow_ids(handle: WorkflowHandle[Any, Any]) -> list[str]:
    history = await handle.fetch_history()
    return [
        event.child_workflow_execution_started_event_attributes.workflow_execution.workflow_id
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED
    ]


def _child_dsl() -> DSLInput:
    return DSLInput(
        title="Attribution child",
        description="Exercise attribution across a real child workflow",
        entrypoint=DSLEntrypoint(ref="child_action"),
        actions=[
            ActionStatement(
                ref="child_action",
                action="core.noop",
                args={},
                retry_policy=ActionRetryPolicy(max_attempts=1, timeout=10),
            )
        ],
    )


def prepared_fanout(
    *, child_wf_id: WorkflowUUID, modes: list[str]
) -> PreparedSubflowResult:
    return PreparedSubflowResult(
        wf_id=child_wf_id,
        dsl=_child_dsl(),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.noop": "tracecat_registry"},
        ),
        trigger_inputs=InlineObject(
            data=[{"mode": mode} for mode in modes],
            typename="list",
        ),
        runtime_configs=DSLConfig(),
    )


def fanout_run_args(
    *, child_wf_id: WorkflowUUID, modes: list[str], fail_strategy: FailStrategy
) -> DSLRunArgs:
    return DSLRunArgs(
        role=_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Attribution fanout parent",
            description="Exercise real child terminal attribution",
            entrypoint=DSLEntrypoint(ref="fanout"),
            actions=[
                ActionStatement(
                    ref="fanout",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    for_each="${{ for var.item in TRIGGER.items }}",
                    args={
                        "workflow_id": child_wf_id.short(),
                        "trigger_inputs": "${{ var.item }}",
                        "wait_strategy": WaitStrategy.WAIT.value,
                        "fail_strategy": fail_strategy.value,
                    },
                )
            ],
        ),
        trigger_inputs=InlineObject(data={"items": modes}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={},
        ),
    )


async def run_retryable_materialization_failure_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """An exhausted storage transport failure is terminally platform-owned."""
    secret = "s3 transport diagnostic must not enter history"
    fault = _MaterializationFault(
        error_factory=lambda: _transport_error(secret),
        failures=2,
    )
    monkeypatch.setattr(action_module, "retrieve_stored_object", fault.retrieve)
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _run_args(max_attempts=2)

    async with (
        test_worker_factory(
            env.client,
            activities=[
                resolve_time_anchor_activity,
                resolve_workflow_concurrency_limits_enabled_activity,
                WorkflowsManagementService.get_error_handler_workflow_id,
            ],
            task_queue=dsl_queue,
        ),
        test_worker_factory(
            env.client,
            activities=[ExecutorActivities.execute_action_activity],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            run_args,
            id=generate_exec_id(run_args.wf_id),
            task_queue=dsl_queue,
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    assert secret not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=fault.attempts,
    )


async def run_non_retryable_materialization_failure_does_not_retry(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """An integrity-like retrieval failure is platform-owned and non-retryable."""
    fault = _MaterializationFault(
        error_factory=lambda: ValueError("synthetic checksum mismatch"),
        failures=3,
    )
    monkeypatch.setattr(action_module, "retrieve_stored_object", fault.retrieve)
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _run_args(max_attempts=3)

    async with (
        test_worker_factory(
            env.client,
            activities=[
                resolve_time_anchor_activity,
                resolve_workflow_concurrency_limits_enabled_activity,
                WorkflowsManagementService.get_error_handler_workflow_id,
            ],
            task_queue=dsl_queue,
        ),
        test_worker_factory(
            env.client,
            activities=[ExecutorActivities.execute_action_activity],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            run_args,
            id=generate_exec_id(run_args.wf_id),
            task_queue=dsl_queue,
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=fault.attempts,
    )


async def run_successful_materialization_retry_does_not_set_terminal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """A transient classified attempt remains invisible after eventual success."""
    secret = "transient transport diagnostic must not enter history"
    fault = _MaterializationFault(
        error_factory=lambda: _transport_error(secret),
        failures=2,
    )
    monkeypatch.setattr(action_module, "retrieve_stored_object", fault.retrieve)
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _run_args(max_attempts=3)

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            await handle.result()

    assert secret not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(root=handle, attempts=fault.attempts)


async def run_executor_internal_failure_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    error_factory: Callable[[], Exception],
    max_attempts: int,
) -> ScenarioObservation:
    """Typed executor internals survive the activity and workflow boundaries."""
    fault = _DispatchFault(error_factory=error_factory, failures=max_attempts)
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _inline_run_args(max_attempts=max_attempts)

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=fault.dispatch,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()

    assert (
        "sandbox supervisor diagnostic" not in (await handle.fetch_history()).to_json()
    )
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=fault.attempts,
    )


async def run_executor_boundary_failure_sets_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    fault_point: ExecutorBoundaryFaultPoint,
) -> ScenarioObservation:
    """Inject one failure at a real executor activity boundary."""
    attempts = 0
    diagnostic = f"{fault_point} diagnostic must not enter history"

    def get_backend() -> object:
        nonlocal attempts
        if fault_point == "backend_initialization":
            attempts += 1
            raise RuntimeError(diagnostic)
        return object()

    async def dispatch(*, backend: object, input: object) -> object:
        nonlocal attempts
        del backend, input
        if fault_point == "loop_platform":
            attempts += 1
            info = ExecutorActionErrorInfo(
                type="RegistryArtifactExtractionError",
                message="masked executor failure",
                action_name="core.noop",
                filename="<executor>",
                function="dispatch",
                loop_iteration=0,
            )
            iteration_error = ExecutionError(info=info)
            iteration_error.__cause__ = RegistryArtifactExtractionError()
            raise LoopExecutionError([iteration_error])
        if fault_point == "entitlement":
            attempts += 1
            cause = EntitlementRequired("synthetic_feature")
            error = ExecutionError(
                info=ExecutorActionErrorInfo(
                    type=type(cause).__name__,
                    message="masked executor failure",
                    action_name="core.noop",
                    filename="<executor>",
                    function="dispatch",
                )
            )
            raise error from cause
        return {"ok": True}

    class _Storage:
        async def store(self, _key: str, _value: object) -> object:
            nonlocal attempts
            assert fault_point == "result_persistence"
            attempts += 1
            raise _transport_error(diagnostic)

    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _inline_run_args(max_attempts=3)
    storage = _Storage()

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            new=get_backend,
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=dispatch,
        ),
        patch(
            "tracecat.executor.activities.get_object_storage",
            return_value=storage,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()

    assert diagnostic not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=attempts,
    )


async def run_successful_registry_contention_retry_has_no_terminal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """Recovered lease contention does not leave terminal attribution behind."""
    fault = _DispatchFault(
        error_factory=lambda: RegistryArtifactCacheLeaseContentionError(
            current_bytes=80,
            additional_bytes=30,
            max_bytes=100,
        ),
        failures=2,
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _inline_run_args(max_attempts=3)

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=fault.dispatch,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            await handle.result()

    return ScenarioObservation(root=handle, attempts=fault.attempts)


async def run_user_action_failure_sets_user_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """A terminal business-logic failure is attributed to the workflow user."""
    diagnostic = "raw child-process diagnostic must not enter history"
    fault = _DispatchFault(
        error_factory=lambda: ValueError(diagnostic),
        failures=2,
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _inline_run_args(max_attempts=2)

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=fault.dispatch,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()

    assert diagnostic not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=fault.attempts,
    )
