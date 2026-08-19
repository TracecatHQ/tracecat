from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from unittest.mock import AsyncMock, patch

import pytest
from botocore.exceptions import HTTPClientError
from temporalio.api.enums.v1 import WorkflowExecutionStatus
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.testing import WorkflowEnvironment

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl import action as action_module
from tracecat.dsl import init_activities as init_activities_module
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.init_activities import (
    resolve_time_anchor_activity,
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.exceptions import ExecutionError
from tracecat.executor.activities import ExecutorActivities
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLeaseContentionError,
    RegistryArtifactExtractionError,
)
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorCode,
    RuntimeErrorOwner,
)
from tracecat.sandbox.exceptions import SandboxExecutionError
from tracecat.storage.object import ExternalObject, InlineObject, ObjectRef
from tracecat.temporal.errors import extract_error_envelope
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.management.definitions import (
    WorkflowDefinitionsService,
    get_workflow_definition_activity,
)
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = [pytest.mark.temporal]


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


async def _describe_owner(handle: WorkflowHandle[Any, Any]) -> str | None:
    description = await handle.describe()
    return description.typed_search_attributes.get(TemporalSearchAttr.ERROR_OWNER.key)


@pytest.mark.anyio
async def test_retryable_materialization_failure_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    envelope = extract_error_envelope(exc_info.value)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.PLATFORM
    assert envelope.code is RuntimeErrorCode.PLATFORM_DEPENDENCY_UNAVAILABLE
    assert envelope.retry_disposition is RetryDisposition.RETRYABLE
    assert fault.attempts == 2

    description = await handle.describe()
    assert (
        description.status == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_FAILED
    )
    assert await _describe_owner(handle) == RuntimeErrorOwner.PLATFORM.value
    assert secret not in (await handle.fetch_history()).to_json()


@pytest.mark.anyio
async def test_non_retryable_materialization_failure_does_not_retry(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    envelope = extract_error_envelope(exc_info.value)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.PLATFORM
    assert envelope.code is RuntimeErrorCode.PLATFORM_UNCLASSIFIED
    assert envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert fault.attempts == 1
    assert await _describe_owner(handle) == RuntimeErrorOwner.PLATFORM.value


@pytest.mark.anyio
async def test_successful_materialization_retry_does_not_set_terminal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    description = await handle.describe()
    assert (
        description.status
        == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED
    )
    assert fault.attempts == 3
    assert await _describe_owner(handle) is None
    assert secret not in (await handle.fetch_history()).to_json()


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "error_factory",
        "max_attempts",
        "expected_attempts",
        "expected_code",
        "expected_retry_disposition",
    ),
    [
        (
            lambda: RegistryArtifactCacheLeaseContentionError(
                current_bytes=80,
                additional_bytes=30,
                max_bytes=100,
            ),
            2,
            2,
            RuntimeErrorCode.PLATFORM_CAPACITY_EXHAUSTED,
            RetryDisposition.RETRYABLE,
        ),
        (
            lambda: RegistryArtifactCacheCapacityError(
                current_bytes=80,
                additional_bytes=30,
                max_bytes=100,
            ),
            3,
            1,
            RuntimeErrorCode.PLATFORM_CAPACITY_EXHAUSTED,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            RegistryArtifactExtractionError,
            3,
            1,
            RuntimeErrorCode.PLATFORM_UNCLASSIFIED,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            lambda: SandboxExecutionError(
                "sandbox supervisor diagnostic must not enter history"
            ),
            2,
            2,
            RuntimeErrorCode.PLATFORM_UNCLASSIFIED,
            RetryDisposition.RETRYABLE,
        ),
    ],
)
async def test_executor_internal_failure_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    error_factory: Callable[[], Exception],
    max_attempts: int,
    expected_attempts: int,
    expected_code: RuntimeErrorCode,
    expected_retry_disposition: RetryDisposition,
) -> None:
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

    envelope = extract_error_envelope(exc_info.value)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.PLATFORM
    assert envelope.code is expected_code
    assert envelope.retry_disposition is expected_retry_disposition
    assert fault.attempts == expected_attempts
    assert await _describe_owner(handle) == RuntimeErrorOwner.PLATFORM.value
    assert (
        "sandbox supervisor diagnostic" not in (await handle.fetch_history()).to_json()
    )


@pytest.mark.anyio
async def test_successful_registry_contention_retry_has_no_terminal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> None:
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

    assert fault.attempts == 3
    assert await _describe_owner(handle) is None


@pytest.mark.anyio
async def test_user_action_failure_sets_user_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> None:
    """A terminal business-logic failure is attributed to the workflow user."""
    fault = _DispatchFault(
        error_factory=lambda: ValueError("synthetic masked user error"),
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

    envelope = extract_error_envelope(exc_info.value)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.USER
    assert envelope.code is RuntimeErrorCode.USER_ACTION_FAILED
    assert envelope.retry_disposition is RetryDisposition.RETRYABLE
    assert fault.attempts == 2
    assert await _describe_owner(handle) == RuntimeErrorOwner.USER.value


@pytest.mark.anyio
async def test_missing_published_definition_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale scheduled run with no published definition is platform-owned."""
    get_definition = AsyncMock(return_value=None)
    monkeypatch.setattr(
        WorkflowDefinitionsService,
        "get_definition_by_workflow_id",
        get_definition,
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    run_args = _inline_run_args(max_attempts=1).model_copy(
        update={"dsl": None, "registry_lock": None}
    )

    async with test_worker_factory(
        env.client,
        activities=[get_workflow_definition_activity],
        task_queue=dsl_queue,
    ):
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            run_args,
            id=generate_exec_id(run_args.wf_id),
            task_queue=dsl_queue,
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    envelope = extract_error_envelope(exc_info.value)
    assert envelope is not None
    assert envelope.owner is RuntimeErrorOwner.PLATFORM
    assert envelope.code is RuntimeErrorCode.PLATFORM_UNCLASSIFIED
    assert envelope.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert get_definition.await_count == 1
    assert await _describe_owner(handle) == RuntimeErrorOwner.PLATFORM.value


@pytest.mark.anyio
async def test_handled_platform_activity_failure_uses_authored_error_edge(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> None:
    """Attribution does not override the workflow's authored control flow."""
    child_action = "core.workflow.execute"
    recovery_action = "core.noop"
    run_args = DSLRunArgs(
        role=_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Handled platform failure",
            description="Exercise a child preparation error edge",
            entrypoint=DSLEntrypoint(ref="call_child"),
            actions=[
                ActionStatement(
                    ref="call_child",
                    action=child_action,
                    args={"workflow_alias": "missing-child"},
                ),
                ActionStatement(
                    ref="recover",
                    action=recovery_action,
                    args={},
                    depends_on=["call_child.error"],
                ),
            ],
        ),
        trigger_inputs=InlineObject(data={"source": "inline-trigger"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={recovery_action: "tracecat_registry"},
        ),
    )
    prepare_subflow = AsyncMock(
        side_effect=RuntimeError("child preparation diagnostic must not enter history")
    )
    dispatch = AsyncMock(return_value={"recovered": True})
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with (
        patch("tracecat.dsl.action._prepare_subflow", new=prepare_subflow),
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch("tracecat.executor.activities.dispatch_action", new=dispatch),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    action_module.DSLActivities.prepare_subflow_activity,
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

    description = await handle.describe()
    assert (
        description.status
        == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED
    )
    assert prepare_subflow.await_count == 1
    assert dispatch.await_count == 1
    assert await _describe_owner(handle) is None
    history_json = (await handle.fetch_history()).to_json()
    assert "child preparation diagnostic" not in history_json


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_operation", "expected_status"),
    [
        (
            "cancel",
            WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CANCELED,
        ),
        (
            "terminate",
            WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_TERMINATED,
        ),
        (
            "timeout",
            WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_TIMED_OUT,
        ),
    ],
)
async def test_engine_terminal_status_does_not_set_error_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    terminal_operation: Literal["cancel", "terminate", "timeout"],
    expected_status: WorkflowExecutionStatus,
) -> None:
    """Cancellation, termination, and workflow timeout retain engine semantics."""
    started = asyncio.Event()

    async def blocking_dispatch(*, backend: object, input: object) -> object:
        del backend, input
        started.set()
        await asyncio.Future[None]()
        raise AssertionError("Blocking dispatch unexpectedly resumed")

    run_args = _inline_run_args(max_attempts=1)
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=blocking_dispatch,
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
            if terminal_operation == "timeout":
                handle = await env.client.start_workflow(
                    DSLWorkflow.run,
                    run_args,
                    id=generate_exec_id(run_args.wf_id),
                    task_queue=dsl_queue,
                    run_timeout=timedelta(seconds=1),
                )
            else:
                handle = await env.client.start_workflow(
                    DSLWorkflow.run,
                    run_args,
                    id=generate_exec_id(run_args.wf_id),
                    task_queue=dsl_queue,
                )

            await asyncio.wait_for(started.wait(), timeout=5)
            if terminal_operation == "cancel":
                await handle.cancel()
            elif terminal_operation == "terminate":
                await handle.terminate(reason="Synthetic attribution test")

            with pytest.raises(WorkflowFailureError):
                await handle.result()
            # Let the Worker consume the terminal event and unwind the blocked
            # activity before its context manager shuts down.
            await asyncio.sleep(0.1)

    description = await handle.describe()
    assert description.status == expected_status
    assert await _describe_owner(handle) is None
