"""Child workflow topology runners for runtime error attribution."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment

from tests.temporal.runtime_error_attribution_harness import (
    ScenarioObservation,
    _ChildDispatch,
    _ConcurrentChildDispatch,
    _inline_run_args,
    _role,
    child_workflow_action,
    child_workflow_ids,
    describe_status,
    fanout_dsl,
    fanout_run_args,
    prepared_child,
    prepared_fanout,
    subflow_run_args,
    wait_for_child_workflow_ids,
)
from tracecat import config
from tracecat.dsl import action as action_module
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    DSLRunArgs,
    PreparedSubflowResult,
)
from tracecat.dsl.enums import (
    FailStrategy,
    PlatformAction,
    StreamErrorHandlingStrategy,
    WaitStrategy,
)
from tracecat.dsl.error_transport import parse_classified_action_error_payload
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
from tracecat.expressions.schemas import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import (
    iter_error_chain,
)
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


async def run_invalid_published_definition_sets_platform_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """Malformed persisted definition data is platform-owned and not retried."""
    diagnostic = "malformed definition diagnostic must not enter history"
    get_definition = AsyncMock(
        return_value=SimpleNamespace(
            content={"title": diagnostic},
            registry_lock=None,
        )
    )
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


async def run_missing_subflow_with_cancelled_sibling_preserves_causal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
    """A missing child definition remains causal when a sibling is cancelled."""
    missing_alias = "deliberately-missing-child"
    slow_started = asyncio.Event()
    slow_cancelled = asyncio.Event()

    async def resolve_missing_alias(*args: object, **kwargs: object) -> None:
        del args, kwargs
        await slow_started.wait()
        return None

    async def dispatch_slow_action(*, backend: object, input: object) -> object:
        del backend, input
        slow_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            slow_cancelled.set()
            raise
        return {"ok": True}  # pragma: no cover - cancelled by the workflow

    management_service = AsyncMock()
    management_service.resolve_workflow_alias.side_effect = resolve_missing_alias
    management_context = AsyncMock()
    management_context.__aenter__.return_value = management_service
    run_args = DSLRunArgs(
        role=_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Missing child with cancelled sibling",
            description="Preserve the causal subflow classification",
            entrypoint=DSLEntrypoint(ref="classified_failure"),
            actions=[
                ActionStatement(
                    ref="classified_failure",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": missing_alias},
                    retry_policy=ActionRetryPolicy(max_attempts=1, timeout=60),
                ),
                ActionStatement(
                    ref="slow_sibling",
                    action="core.noop",
                    args={},
                    retry_policy=ActionRetryPolicy(max_attempts=1, timeout=60),
                ),
            ],
        ),
        trigger_inputs=InlineObject(data={}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.noop": "tracecat_registry"},
        ),
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"

    with (
        patch(
            "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
            return_value=management_context,
        ),
        patch(
            "tracecat.executor.activities.get_executor_backend",
            return_value=object(),
        ),
        patch(
            "tracecat.executor.activities.dispatch_action",
            new=dispatch_slow_action,
        ),
    ):
        async with (
            test_worker_factory(
                env.client,
                activities=[
                    resolve_time_anchor_activity,
                    resolve_workflow_concurrency_limits_enabled_activity,
                    action_module.DSLActivities.prepare_subflow_activity,
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

    assert slow_started.is_set()
    assert slow_cancelled.is_set()
    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        attempts=management_service.resolve_workflow_alias.await_count,
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


async def run_concurrent_sibling_cancellation_preserves_causal_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """A raw custom-action failure remains causal through sibling cancellation."""
    action_name = "custom_actions.synthetic.fetch_data"
    leaf_wf_id = WorkflowUUID.new_uuid4()
    intermediary_wf_id = WorkflowUUID.new_uuid4()
    intermediary_dsl = fanout_dsl(
        child_wf_id=leaf_wf_id,
        fail_strategy=FailStrategy.ALL,
    )
    intermediary_lock = RegistryLock(
        origins={"tracecat_registry": "test"},
        actions={},
    )

    async def prepare_subflow(
        input: action_module.PrepareSubflowActivityInput,
    ) -> PreparedSubflowResult:
        match input.task.ref:
            case "run_intermediary":
                return prepared_child(
                    child_wf_id=intermediary_wf_id,
                    dsl=intermediary_dsl,
                    registry_lock=intermediary_lock,
                )
            case "fanout":
                return prepared_fanout(
                    child_wf_id=leaf_wf_id,
                    modes=["user", "slow"],
                    action_name=action_name,
                )
            case _:
                raise AssertionError(f"Unexpected subflow task: {input.task.ref}")

    root_wf_id = WorkflowUUID.new_uuid4()
    run_args = subflow_run_args(
        wf_id=root_wf_id,
        title="Concurrent attribution root",
        description="Propagate causal attribution through an intermediary",
        actions=[
            child_workflow_action(
                ref="run_intermediary",
                child_wf_id=intermediary_wf_id,
                trigger_inputs={"items": ["user", "slow"]},
            )
        ],
    )
    prepare_subflow_mock = AsyncMock(side_effect=prepare_subflow)
    dispatch = _ConcurrentChildDispatch(
        user_diagnostic="causal leaf diagnostic must not enter history",
        calls=[],
        slow_started=asyncio.Event(),
    )
    dsl_queue = f"runtime-error-attribution-{uuid.uuid4()}"
    monkeypatch.setattr(config, "TRACECAT__ACTIVITY_HEARTBEAT_INTERVAL", 1)

    with (
        patch("tracecat.dsl.action._prepare_subflow", new=prepare_subflow_mock),
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
                    action_module.DSLActivities.evaluate_templated_object_activity,
                ],
                task_queue=dsl_queue,
            ),
            test_worker_factory(
                env.client,
                activities=[ExecutorActivities.execute_action_activity],
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            ),
        ):
            root_handle = await env.client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=generate_exec_id(root_wf_id),
                task_queue=dsl_queue,
            )
            [intermediary_id] = await wait_for_child_workflow_ids(
                root_handle,
                count=1,
            )
            intermediary_handle = env.client.get_workflow_handle(intermediary_id)
            leaf_ids = await wait_for_child_workflow_ids(
                intermediary_handle,
                count=2,
            )
            leaf_handles = [
                env.client.get_workflow_handle(child_id) for child_id in leaf_ids
            ]
            for _ in range(200):
                statuses = [await describe_status(child) for child in leaf_handles]
                if WorkflowExecutionStatus.FAILED in statuses:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Expected the causal leaf workflow to fail")

            [slow_leaf_handle] = [
                child
                for child, status in zip(leaf_handles, statuses, strict=True)
                if status == WorkflowExecutionStatus.RUNNING
            ]
            await slow_leaf_handle.cancel()
            with pytest.raises(WorkflowFailureError) as root_exc_info:
                await root_handle.result()

    with pytest.raises(WorkflowFailureError) as intermediary_exc_info:
        await intermediary_handle.result()
    terminal_error = next(
        error
        for error in iter_error_chain(intermediary_exc_info.value)
        if isinstance(error, ApplicationError)
    )
    payload = parse_classified_action_error_payload(terminal_error.details[0])
    assert isinstance(payload, dict)
    assert set(payload) == {"fanout"}
    aggregate = payload["fanout"].diagnostic
    assert aggregate is not None
    assert aggregate.children is not None
    assert {child.ref for child in aggregate.children} == {"fanout[0]", "fanout[1]"}
    assert {child.type for child in aggregate.children} >= {"ChildWorkflowError"}

    histories = [
        (await root_handle.fetch_history()).to_json(),
        (await intermediary_handle.fetch_history()).to_json(),
    ]
    for child in leaf_handles:
        histories.append((await child.fetch_history()).to_json())
    assert dispatch.user_diagnostic not in "\n".join(histories)
    assert sorted(dispatch.calls) == ["slow", "user"]
    assert prepare_subflow_mock.await_count == 2
    return ScenarioObservation(
        root=root_handle,
        failure=root_exc_info.value,
        children=(intermediary_handle, *leaf_handles),
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


async def run_invalid_child_input_with_successful_error_handler_sets_user_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> ScenarioObservation:
    """Invalid child input attributes both failed child and terminal parent."""
    handler_wf_id = WorkflowUUID.new_uuid4()
    handler_dsl = DSLInput(
        title="Trigger validation error handler",
        description="Complete successfully after invalid child input",
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

    child_wf_id = WorkflowUUID.new_uuid4()
    child_dsl = DSLInput(
        title="Validated child",
        description="Reject invalid trigger input before action execution",
        entrypoint=DSLEntrypoint(
            ref="child_action",
            expects={"number": ExpectedField(type="int")},
        ),
        actions=[
            ActionStatement(
                ref="child_action",
                action="core.noop",
                args={},
                retry_policy=ActionRetryPolicy(max_attempts=1, timeout=10),
            )
        ],
    )
    prepared = PreparedSubflowResult(
        wf_id=child_wf_id,
        dsl=child_dsl,
        registry_lock=handler_lock,
    )
    prepare_subflow = AsyncMock(return_value=prepared)

    parent_wf_id = WorkflowUUID.new_uuid4()
    run_args = DSLRunArgs(
        role=_role(),
        wf_id=parent_wf_id,
        dsl=DSLInput(
            title="Invalid child input parent",
            description="Run a successful handler after child validation fails",
            entrypoint=DSLEntrypoint(ref="call_child"),
            actions=[
                ActionStatement(
                    ref="call_child",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={
                        "workflow_id": child_wf_id.short(),
                        "trigger_inputs": {"number": "not-an-int"},
                        "wait_strategy": WaitStrategy.WAIT.value,
                    },
                )
            ],
            error_handler=handler_wf_id.short(),
        ),
        trigger_inputs=InlineObject(data={}),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={},
        ),
    )
    dispatch = _ChildDispatch(
        user_diagnostic="unused user diagnostic",
        platform_diagnostic="unused platform diagnostic",
        calls=[],
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
                    get_workflow_definition_activity,
                    action_module.DSLActivities.prepare_subflow_activity,
                    action_module.DSLActivities.evaluate_templated_object_activity,
                    action_module.DSLActivities.normalize_trigger_inputs_activity,
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

    child_handles = [
        env.client.get_workflow_handle(child_id)
        for child_id in await child_workflow_ids(parent_handle)
    ]
    assert len(child_handles) == 2
    assert handler_definition.await_count == 1
    assert prepare_subflow.await_count == 1
    assert dispatch.calls == ["success"]
    return ScenarioObservation(
        root=parent_handle,
        failure=exc_info.value,
        children=tuple(child_handles),
    )
