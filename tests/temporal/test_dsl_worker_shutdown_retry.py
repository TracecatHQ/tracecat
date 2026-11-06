"""DSL actions interrupted by an executor drain timeout (``WorkerShutdown``).

Invariants:
- An action with ``max_attempts == 1`` whose executor drains before it finishes
  is dispatched exactly once more, and the workflow can still succeed.
- When the retry is also interrupted, the terminal failure is the named
  platform kind ``executor.worker.shutdown`` with a retryable disposition,
  never ``runtime.unclassified``.
- Histories recorded before the patch replay without nondeterminism.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tests.shared import recorded_patch_ids
from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl import workflow as workflow_module
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.action import DSLActivities
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.init_activities import (
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.interceptor import RuntimeErrorAttributionInterceptor
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement, RunActionInput
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import extract_error_classification
from tracecat.temporal.patches import WorkflowPatch
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import GetErrorHandlerWorkflowIDActivityInputs

pytestmark = [pytest.mark.temporal, pytest.mark.integration]

_ACTION_RESULT = {"ok": True}


def _activity_name(activity_def: object) -> str:
    """Read the name Temporal attaches dynamically to an activity definition."""
    return getattr(activity_def, "__temporal_activity_definition").name


@activity.defn(
    name=_activity_name(resolve_workflow_concurrency_limits_enabled_activity)
)
async def _disable_workflow_concurrency_limits() -> bool:
    return False


@activity.defn(
    name=_activity_name(WorkflowsManagementService.get_error_handler_workflow_id)
)
async def _no_error_handler(_input: GetErrorHandlerWorkflowIDActivityInputs) -> None:
    return None


def _worker_shutdown() -> ApplicationError:
    """The failure sdk-core records when a drain outlives the graceful timeout."""
    return ApplicationError(
        "Worker is shutting down and this activity did not complete in time",
        type="WorkerShutdown",
    )


@dataclass(slots=True)
class _ScriptedExecutor:
    """Fake ``execute_action_activity`` failing its first N dispatches."""

    failures: list[BaseException]
    dispatches: int = 0
    task_refs: list[str] = field(default_factory=list)

    def activity(self) -> Callable[[RunActionInput, Role], object]:
        @activity.defn(name="execute_action_activity")
        async def execute_action_activity(
            input: RunActionInput, _role: Role
        ) -> InlineObject:
            self.dispatches += 1
            self.task_refs.append(input.task.ref)
            if self.failures:
                raise self.failures.pop(0)
            return InlineObject(data=_ACTION_RESULT)

        return execute_action_activity


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(compression_enabled=False),
        search_attributes=[TemporalSearchAttr.ERROR_OWNER.key],
        dev_server_log_level="error",
    ) as environment:
        yield environment


def _run_args(*, max_attempts: int = 1) -> DSLRunArgs:
    return DSLRunArgs(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        ),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Worker shutdown retry probe",
            description="Interrupt an action with an executor drain timeout",
            entrypoint=DSLEntrypoint(ref="run"),
            actions=[
                ActionStatement(
                    ref="run",
                    action="core.noop",
                    args={},
                    retry_policy=ActionRetryPolicy(max_attempts=max_attempts),
                )
            ],
        ),
        trigger_inputs=InlineObject(data={"source": "worker-shutdown-test"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.noop": "tracecat_registry"},
        ),
        time_anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _dsl_worker(client: Client, task_queue: str) -> Worker:
    return Worker(
        client=client,
        task_queue=task_queue,
        activities=[
            _disable_workflow_concurrency_limits,
            DSLActivities.compile_dsl_dependencies_activity,
            _no_error_handler,
        ],
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        interceptors=[RuntimeErrorAttributionInterceptor()],
    )


async def _start(
    client: Client, task_queue: str, run_args: DSLRunArgs
) -> WorkflowHandle[DSLWorkflow, object]:
    return await client.start_workflow(
        DSLWorkflow.run,
        run_args,
        id=generate_exec_id(run_args.wf_id),
        task_queue=task_queue,
        execution_timeout=timedelta(seconds=60),
    )


async def _run_to_failure(
    client: Client, run_args: DSLRunArgs, executor: _ScriptedExecutor
) -> tuple[WorkflowHandle[DSLWorkflow, object], WorkflowFailureError]:
    task_queue = f"dsl-worker-shutdown-{uuid.uuid4()}"
    async with (
        _dsl_worker(client, task_queue),
        Worker(
            client=client,
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            activities=[executor.activity()],
        ),
    ):
        handle = await _start(client, task_queue, run_args)
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()
    return handle, exc_info.value


async def _assert_replays(client: Client, handle: WorkflowHandle) -> None:
    history = await handle.fetch_history()
    replay_result = await Replayer(
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        data_converter=client.data_converter,
        interceptors=[RuntimeErrorAttributionInterceptor()],
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert replay_result.replay_failure is None


@pytest.mark.anyio
async def test_real_executor_drain_timeout_retries_action_on_next_executor(
    env: WorkflowEnvironment,
) -> None:
    """A real worker drain cut short by its graceful timeout is retried once."""
    client = env.client
    task_queue = f"dsl-worker-shutdown-{uuid.uuid4()}"
    run_args = _run_args()
    started = asyncio.Event()
    dispatches = 0

    @activity.defn(name="execute_action_activity")
    async def draining_action(_input: RunActionInput, _role: Role) -> InlineObject:
        nonlocal dispatches
        dispatches += 1
        started.set()
        # Outlive the drain; sdk-core then fails the attempt with WorkerShutdown.
        while True:
            activity.heartbeat()
            await asyncio.sleep(0.1)

    @activity.defn(name="execute_action_activity")
    async def completing_action(_input: RunActionInput, _role: Role) -> InlineObject:
        nonlocal dispatches
        dispatches += 1
        return InlineObject(data=_ACTION_RESULT)

    async with _dsl_worker(client, task_queue):
        draining_executor = Worker(
            client=client,
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            activities=[draining_action],
            graceful_shutdown_timeout=timedelta(seconds=1),
        )
        draining_run = asyncio.create_task(draining_executor.run())
        handle = await _start(client, task_queue, run_args)
        await asyncio.wait_for(started.wait(), timeout=30)
        await draining_executor.shutdown()
        await draining_run

        async with Worker(
            client=client,
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            activities=[completing_action],
        ):
            await asyncio.wait_for(handle.result(), timeout=30)

    assert dispatches == 2
    history = await handle.fetch_history()
    failed_attempts = [
        event
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
    ]
    assert len(failed_attempts) == 1
    recorded_failure = await client.data_converter.decode_failure(
        failed_attempts[0].activity_task_failed_event_attributes.failure
    )
    assert isinstance(recorded_failure, ApplicationError)
    assert recorded_failure.type == "WorkerShutdown"
    patch_ids = await recorded_patch_ids(client, history)
    assert WorkflowPatch.ACTION_WORKER_SHUTDOWN_RETRY in patch_ids
    await _assert_replays(client, handle)


@pytest.mark.anyio
async def test_second_worker_shutdown_is_classified_platform_retryable(
    env: WorkflowEnvironment,
) -> None:
    """A retry that is also drained fails with the named worker-shutdown kind."""
    client = env.client
    executor = _ScriptedExecutor(failures=[_worker_shutdown(), _worker_shutdown()])

    handle, failure = await _run_to_failure(client, _run_args(), executor)

    assert executor.dispatches == 2
    classification = extract_error_classification(failure)
    assert classification is not None
    assert classification.kind is RuntimeErrorKind.EXECUTOR_WORKER_SHUTDOWN
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert (await handle.describe()).typed_search_attributes.get(
        TemporalSearchAttr.ERROR_OWNER.key
    ) == RuntimeErrorOwner.PLATFORM.value
    await _assert_replays(client, handle)


@pytest.mark.anyio
async def test_configured_retries_get_no_extra_dispatch_but_are_classified(
    env: WorkflowEnvironment,
) -> None:
    """Temporal owns retries when ``max_attempts > 1``; the DSL adds none."""
    client = env.client
    executor = _ScriptedExecutor(failures=[_worker_shutdown(), _worker_shutdown()])

    _handle, failure = await _run_to_failure(
        client, _run_args(max_attempts=2), executor
    )

    assert executor.dispatches == 2
    classification = extract_error_classification(failure)
    assert classification is not None
    assert classification.kind is RuntimeErrorKind.EXECUTOR_WORKER_SHUTDOWN


@pytest.mark.anyio
async def test_other_application_errors_are_not_retried(
    env: WorkflowEnvironment,
) -> None:
    """Only the drain-timeout failure type earns the extra dispatch."""
    client = env.client
    executor = _ScriptedExecutor(
        failures=[ApplicationError("Action failed", type="SomethingElse")]
    )

    handle, failure = await _run_to_failure(client, _run_args(), executor)

    assert executor.dispatches == 1
    classification = extract_error_classification(failure)
    assert classification is None or (
        classification.kind is not RuntimeErrorKind.EXECUTOR_WORKER_SHUTDOWN
    )
    history = await handle.fetch_history()
    patch_ids = await recorded_patch_ids(client, history)
    assert WorkflowPatch.ACTION_WORKER_SHUTDOWN_RETRY not in patch_ids


@pytest.mark.anyio
async def test_pre_patch_worker_shutdown_history_replays(
    env: WorkflowEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A marker-free WorkerShutdown failure replays on the patched workflow."""
    client = env.client
    executor = _ScriptedExecutor(failures=[_worker_shutdown()])
    current_patched = workflow_module.workflow.patched

    def pre_patch(patch_id: str) -> bool:
        if patch_id == WorkflowPatch.ACTION_WORKER_SHUTDOWN_RETRY:
            return False
        return current_patched(patch_id)

    with monkeypatch.context() as legacy_worker:
        legacy_worker.setattr(workflow_module.workflow, "patched", pre_patch)
        handle, failure = await _run_to_failure(client, _run_args(), executor)

    # The pre-patch behavior: one dispatch, an unclassified terminal failure.
    assert executor.dispatches == 1
    classification = extract_error_classification(failure)
    assert classification is not None
    assert classification.kind is RuntimeErrorKind.RUNTIME_UNCLASSIFIED
    history = await handle.fetch_history()
    patch_ids = await recorded_patch_ids(client, history)
    assert WorkflowPatch.ACTION_WORKER_SHUTDOWN_RETRY not in patch_ids

    await _assert_replays(client, handle)
