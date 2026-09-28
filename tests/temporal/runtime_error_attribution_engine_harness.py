"""Authored-edge and engine-terminal runtime attribution runners."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

from tests.temporal.runtime_error_attribution_harness import (
    ScenarioObservation,
    _inline_run_args,
    _role,
)
from tracecat import config
from tracecat.dsl import action as action_module
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.init_activities import (
    resolve_time_anchor_activity,
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.activities import ExecutorActivities
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.workflow.management.management import WorkflowsManagementService


async def run_handled_platform_activity_failure_uses_authored_error_edge(
    env: WorkflowEnvironment,
    test_worker_factory,
) -> ScenarioObservation:
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

    assert prepare_subflow.await_count == 1
    assert dispatch.await_count == 1
    history_json = (await handle.fetch_history()).to_json()
    assert "child preparation diagnostic" not in history_json
    return ScenarioObservation(root=handle)


async def run_engine_terminal_status_does_not_set_error_owner(
    env: WorkflowEnvironment,
    test_worker_factory,
    terminal_operation: Literal["cancel", "terminate", "timeout"],
) -> ScenarioObservation:
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

            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()
            # Let the Worker consume the terminal event and unwind the blocked
            # activity before its context manager shuts down.
            await asyncio.sleep(0.1)

    return ScenarioObservation(root=handle, failure=exc_info.value)
