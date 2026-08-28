"""Child workflow topology runners for runtime error attribution."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

from tests.temporal.runtime_error_attribution_harness import (
    ScenarioObservation,
    _ChildDispatch,
    _inline_run_args,
    _role,
    child_workflow_ids,
    describe_status,
    fanout_run_args,
    prepared_fanout,
)
from tracecat import config
from tracecat.dsl import action as action_module
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import (
    FailStrategy,
    StreamErrorHandlingStrategy,
)
from tracecat.dsl.init_activities import (
    resolve_time_anchor_activity,
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.schemas import (
    ActionRetryPolicy,
    ActionStatement,
    GatherArgs,
    ScatterArgs,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.activities import ExecutorActivities
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.workflow.management.definitions import (
    WorkflowDefinitionsService,
    get_workflow_definition_activity,
)
from tracecat.workflow.management.management import WorkflowsManagementService


async def run_missing_published_definition_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
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

    assert get_definition.await_count == 1
    return ScenarioObservation(root=handle, failure=exc_info.value, attempts=1)


async def run_definition_lookup_failure_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """A definition-store failure is attributed at the lookup boundary."""
    diagnostic = "definition database diagnostic must not enter history"
    get_definition = AsyncMock(side_effect=RuntimeError(diagnostic))
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

    assert diagnostic not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=get_definition.await_count,
    )


async def run_unhandled_subflow_preparation_failure_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """An unhandled preparation failure attributes the parent execution."""
    diagnostic = "child preparation diagnostic must not enter history"
    prepare_subflow = AsyncMock(side_effect=RuntimeError(diagnostic))
    run_args = DSLRunArgs(
        role=_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Unhandled platform failure",
            description="Exercise terminal child preparation attribution",
            entrypoint=DSLEntrypoint(ref="call_child"),
            actions=[
                ActionStatement(
                    ref="call_child",
                    action="core.workflow.execute",
                    args={"workflow_alias": "missing-child"},
                )
            ],
        ),
        trigger_inputs=InlineObject(data={"source": "inline-trigger"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={},
        ),
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with patch("tracecat.dsl.action._prepare_subflow", new=prepare_subflow):
        async with test_worker_factory(
            env.client,
            activities=[
                resolve_time_anchor_activity,
                resolve_workflow_concurrency_limits_enabled_activity,
                action_module.DSLActivities.prepare_subflow_activity,
                WorkflowsManagementService.get_error_handler_workflow_id,
            ],
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

    assert diagnostic not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=prepare_subflow.await_count,
    )


async def run_gather_raise_preserves_platform_child_attribution(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """A gather activity round-trip retains a classified stream failure."""
    diagnostic = "gather child diagnostic must not enter history"
    prepare_subflow = AsyncMock(side_effect=RuntimeError(diagnostic))
    run_args = DSLRunArgs(
        role=_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Classified gather failure",
            description="Exercise classified errors across a gather activity",
            entrypoint=DSLEntrypoint(ref="scatter"),
            actions=[
                ActionStatement(
                    ref="scatter",
                    action="core.transform.scatter",
                    args=ScatterArgs(collection=[1]).model_dump(),
                ),
                ActionStatement(
                    ref="call_child",
                    action="core.workflow.execute",
                    depends_on=["scatter"],
                    args={"workflow_alias": "missing-child"},
                ),
                ActionStatement(
                    ref="gather",
                    action="core.transform.gather",
                    depends_on=["call_child"],
                    args=GatherArgs(
                        items="${{ ACTIONS.call_child.result }}",
                        error_strategy=StreamErrorHandlingStrategy.RAISE,
                    ).model_dump(),
                ),
            ],
        ),
        trigger_inputs=InlineObject(data={"source": "inline-trigger"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={},
        ),
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with patch("tracecat.dsl.action._prepare_subflow", new=prepare_subflow):
        async with test_worker_factory(
            env.client,
            activities=[
                resolve_time_anchor_activity,
                resolve_workflow_concurrency_limits_enabled_activity,
                action_module.DSLActivities.handle_scatter_input_activity,
                action_module.DSLActivities.prepare_subflow_activity,
                action_module.DSLActivities.finalize_gather_activity,
                WorkflowsManagementService.get_error_handler_workflow_id,
            ],
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

    assert prepare_subflow.await_count == 1, str(exc_info.value.cause)
    assert diagnostic not in (await handle.fetch_history()).to_json()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=prepare_subflow.await_count,
    )


async def run_isolated_platform_child_failure_does_not_attribute_parent(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """A real failed child remains alertable when its parent completes."""
    child_wf_id = WorkflowUUID.new_uuid4()
    modes = ["platform", "success"]
    prepared = prepared_fanout(child_wf_id=child_wf_id, modes=modes)
    prepare_subflow = AsyncMock(return_value=prepared)
    dispatch = _ChildDispatch(
        user_diagnostic="unused user diagnostic",
        platform_diagnostic="child platform diagnostic must not enter history",
        calls=[],
    )
    run_args = fanout_run_args(
        child_wf_id=child_wf_id,
        modes=modes,
        fail_strategy=FailStrategy.ISOLATED,
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with (
        patch("tracecat.dsl.action._prepare_subflow", new=prepare_subflow),
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=dispatch.dispatch,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                    action_module.DSLActivities.prepare_subflow_activity,
                    action_module.DSLActivities.synchronize_collection_object_activity,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            parent_handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            await parent_handle.result()

    child_handles = [
        env.client.get_workflow_handle(child_id)
        for child_id in await child_workflow_ids(parent_handle)
    ]
    assert len(child_handles) == 2
    child_statuses = [await describe_status(child) for child in child_handles]
    failed_status = int(WorkflowExecutionStatus.FAILED)
    assert child_statuses.count(failed_status) == 1

    failed_child = child_handles[child_statuses.index(failed_status)]
    assert prepare_subflow.await_count == 1
    assert sorted(dispatch.calls) == ["platform", "success"]

    diagnostic = dispatch.platform_diagnostic
    assert diagnostic not in (await failed_child.fetch_history()).to_json()
    assert diagnostic not in (await parent_handle.fetch_history()).to_json()
    return ScenarioObservation(root=parent_handle, children=tuple(child_handles))


async def run_fail_all_preserves_mixed_child_attribution(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """A failing fanout keeps per-child attribution and alerts as platform.

    Each failed child stays independently attributed through its own execution;
    the parent's terminal envelope carries only the selected classification.
    """
    child_wf_id = WorkflowUUID.new_uuid4()
    modes = ["user", "platform", "success"]
    prepared = prepared_fanout(child_wf_id=child_wf_id, modes=modes)
    prepare_subflow = AsyncMock(return_value=prepared)
    dispatch = _ChildDispatch(
        user_diagnostic="child user diagnostic must not enter history",
        platform_diagnostic="child platform diagnostic must not enter history",
        calls=[],
    )
    run_args = fanout_run_args(
        child_wf_id=child_wf_id,
        modes=modes,
        fail_strategy=FailStrategy.ALL,
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with (
        patch("tracecat.dsl.action._prepare_subflow", new=prepare_subflow),
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=dispatch.dispatch,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                    action_module.DSLActivities.prepare_subflow_activity,
                    action_module.DSLActivities.synchronize_collection_object_activity,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            parent_handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(run_args.wf_id),
                task_queue=dsl_queue,
            )
            with pytest.raises(WorkflowFailureError) as exc_info:
                await parent_handle.result()

    child_handles = [
        env.client.get_workflow_handle(child_id)
        for child_id in await child_workflow_ids(parent_handle)
    ]
    histories = [(await parent_handle.fetch_history()).to_json()]
    for child in child_handles:
        histories.append((await child.fetch_history()).to_json())
    serialized_histories = "\n".join(histories)
    assert dispatch.user_diagnostic not in serialized_histories
    assert dispatch.platform_diagnostic not in serialized_histories
    return ScenarioObservation(
        root=parent_handle,
        failure=exc_info.value,
        children=tuple(child_handles),
    )


async def run_successful_error_handler_does_not_inherit_terminal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """A successful handler child is not mislabeled by its failing parent."""
    handler_wf_id = WorkflowUUID.new_uuid4()
    handler_dsl = DSLInput(
        title="Attribution error handler",
        description="Complete successfully after a classified parent failure",
        entrypoint=DSLEntrypoint(ref="handle"),
        actions=[
            ActionStatement(
                ref="handle",
                action="core.noop",
                args={},
                retry_policy=ActionRetryPolicy(max_attempts=1, timeout=10),
            )
        ],
    )
    handler_lock = RegistryLock(
        origins={"tracecat_registry": "test"},
        actions={"core.noop": "tracecat_registry"},
    )
    handler_definition = AsyncMock(
        return_value=SimpleNamespace(
            content=handler_dsl.model_dump(),
            registry_lock=handler_lock.model_dump(),
        )
    )
    monkeypatch.setattr(
        WorkflowDefinitionsService,
        "get_definition_by_workflow_id",
        handler_definition,
    )

    parent_wf_id = WorkflowUUID.new_uuid4()
    run_args = DSLRunArgs(
        role=_role(),
        wf_id=parent_wf_id,
        dsl=DSLInput(
            title="Failing parent with handler",
            description="Verify terminal attribution stays on the failed execution",
            entrypoint=DSLEntrypoint(ref="fail"),
            actions=[
                ActionStatement(
                    ref="fail",
                    action="core.noop",
                    args={},
                    retry_policy=ActionRetryPolicy(max_attempts=1, timeout=10),
                )
            ],
            error_handler=handler_wf_id.short(),
        ),
        trigger_inputs=InlineObject(data={"mode": "user"}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.noop": "tracecat_registry"},
        ),
    )
    dispatch = _ChildDispatch(
        user_diagnostic="parent user diagnostic must not enter history",
        platform_diagnostic="unused platform diagnostic",
        calls=[],
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with (
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=dispatch.dispatch,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    WorkflowsManagementService.get_error_handler_workflow_id,
                    get_workflow_definition_activity,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            parent_handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(parent_wf_id),
                task_queue=dsl_queue,
            )
            with pytest.raises(WorkflowFailureError) as exc_info:
                await parent_handle.result()

    [handler_id] = await child_workflow_ids(parent_handle)
    handler_handle = env.client.get_workflow_handle(handler_id)
    assert handler_definition.await_count == 1
    assert dispatch.calls == ["user", "success"]

    histories = "\n".join(
        [
            (await parent_handle.fetch_history()).to_json(),
            (await handler_handle.fetch_history()).to_json(),
        ]
    )
    assert dispatch.user_diagnostic not in histories
    return ScenarioObservation(
        root=parent_handle,
        failure=exc_info.value,
        children=(handler_handle,),
    )
