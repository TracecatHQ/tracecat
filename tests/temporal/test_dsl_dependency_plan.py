"""Exercise dependency compilation and replay across real activity boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tests.shared import recorded_patch_ids
from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.action import (
    DSLActivities,
    EvaluateTemplatedObjectActivityInput,
    materialize_context,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionStatement, RunActionInput
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.expressions.eval import eval_templated_object
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject, StoredObject
from tracecat.temporal.patches import WorkflowPatch

pytestmark = [pytest.mark.temporal, pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@activity.defn(name="resolve_workflow_concurrency_limits_enabled_activity")
async def _disable_limits() -> bool:
    return False


@activity.defn(name="execute_action_activity")
async def _produce_result(input: RunActionInput, _role: Role) -> StoredObject:
    if input.task.ref == "summary":
        assert set(input.exec_context["ACTIONS"]) == {"step_0"}
        return InlineObject(data={"count": input.task.args["count"]})
    assert input.exec_context["ACTIONS"] == {}
    return InlineObject(data="x" * (100 * 1024))


@activity.defn(name="resolve_return_expression_activity")
async def _evaluate_return(input: EvaluateTemplatedObjectActivityInput) -> StoredObject:
    operand = await materialize_context(input.operand)
    return InlineObject(data=eval_templated_object(input.obj, operand=operand))


@workflow.defn(name="DSLWorkflow", sandboxed=False)
class _BeforeDependencyCompilationWorkflow(DSLWorkflow):
    """Capture the former command sequence without the compilation marker."""

    @workflow.run
    async def run(self, args: DSLRunArgs) -> StoredObject:
        return await super().run(args)

    async def _compile_dependencies(self) -> None:
        pass


def _run_args(count: int) -> DSLRunArgs:
    return DSLRunArgs(
        role=Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid4(),
            organization_id=uuid4(),
        ),
        wf_id=WorkflowUUID.new_uuid4(),
        dsl=DSLInput(
            title="Activity dependency payload probe",
            entrypoint=DSLEntrypoint(ref="step_0"),
            description="Synthetic accumulated inline results",
            actions=[
                *[
                    ActionStatement(
                        ref=f"step_{i}",
                        action="core.noop",
                        depends_on=[f"step_{i - 1}"] if i else [],
                    )
                    for i in range(count)
                ],
                ActionStatement(
                    ref="summary",
                    action="core.noop",
                    depends_on=[f"step_{count - 1}"],
                    args={"input": "${{ ACTIONS.step_0.result }}", "count": count},
                ),
            ],
            returns="${{ ACTIONS.summary.result }}",
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test"},
            actions={"core.noop": "tracecat_registry"},
        ),
        time_anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize("compiled", [True, False])
async def test_dependency_plan_activity_payloads_and_replay(compiled: bool) -> None:
    # New runs exceed 2 MiB of accumulated inline data. Keep old runs below the
    # activity limit so we can capture a successful pre-patch return history.
    count = 24 if compiled else 3
    args = _run_args(count)
    task_queue = f"dependency-plan-{uuid4()}"
    workflow_class = DSLWorkflow if compiled else _BeforeDependencyCompilationWorkflow
    converter = get_data_converter(compression_enabled=False)
    async with await WorkflowEnvironment.start_local(
        data_converter=converter,
        dev_server_log_level="error",
    ) as env:
        async with (
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[workflow_class],
                workflow_runner=new_sandbox_runner(),
                activities=[
                    _disable_limits,
                    DSLActivities.compile_dsl_dependencies_activity,
                    _evaluate_return,
                ],
            ),
            Worker(
                env.client,
                task_queue=config.TRACECAT__EXECUTOR_QUEUE,
                activities=[_produce_result],
            ),
        ):
            handle = await env.client.start_workflow(
                "DSLWorkflow",
                args,
                id=generate_exec_id(args.wf_id),
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=60),
                result_type=InlineObject,
            )
            assert await handle.result() == InlineObject(data={"count": count})
        history = await handle.fetch_history()
        patch_ids = await recorded_patch_ids(env.client, history)
        assert (WorkflowPatch.COMPILE_DSL_DEPENDENCIES in patch_ids) is compiled
        scheduled = [
            event.activity_task_scheduled_event_attributes
            for event in history.events
            if event.HasField("activity_task_scheduled_event_attributes")
        ]
        compile_calls = [
            event
            for event in scheduled
            if event.activity_type.name == "compile_dsl_dependencies_activity"
        ]
        assert len(compile_calls) == int(compiled)
        return_call = next(
            event
            for event in scheduled
            if event.activity_type.name == "resolve_return_expression_activity"
        )
        [return_input] = await converter.decode(
            return_call.input.payloads, [EvaluateTemplatedObjectActivityInput]
        )
        assert set(return_input.operand["ACTIONS"]) == (
            {"summary"} if compiled else {"summary", "step_0", "step_1", "step_2"}
        )
        if compiled:
            assert return_call.input.ByteSize() < 2048
        else:
            assert return_call.input.ByteSize() > 300 * 1024
        await Replayer(
            workflows=[DSLWorkflow],
            workflow_runner=new_sandbox_runner(),
            data_converter=converter,
        ).replay_workflow(history)
