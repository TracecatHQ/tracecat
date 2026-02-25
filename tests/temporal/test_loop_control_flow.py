"""Tests for do-while loop control flow (`core.loop.start` / `core.loop.end`)."""

import os
from collections.abc import Callable
from typing import Any

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id, to_data
from tracecat.auth.types import Role
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.workflow import DSLWorkflow

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]


async def action_result(result: dict[str, Any], ref: str) -> Any:
    """Unwrap StoredObject action result from workflow context."""
    return await to_data(result["ACTIONS"][ref]["result"])


@pytest.mark.anyio
@pytest.mark.integration
async def test_loop_runs_body_once_when_condition_false(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_loop_runs_body_once_when_condition_false.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Loop runs once",
        description="Do-while loop should run body at least once",
        entrypoint=DSLEntrypoint(ref="loop_start"),
        actions=[
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
            ),
            ActionStatement(
                ref="body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["body"],
                args={"condition": "${{ False }}"},
            ),
            ActionStatement(
                ref="after_loop",
                action="core.transform.reshape",
                depends_on=["loop_end"],
                args={"value": "${{ ACTIONS.loop_end.result.continue }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert await action_result(result, "loop_start") == {"iteration": 0}
    assert await action_result(result, "body") == 0
    assert await action_result(result, "after_loop") is False
    assert await action_result(result, "loop_end") == {"continue": False}


@pytest.mark.anyio
@pytest.mark.integration
async def test_loop_index_advances_and_overwrites_body_result(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_loop_index_advances_and_overwrites_body_result.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Loop advances index",
        description="Loop should expose 0-based index and keep latest body result",
        entrypoint=DSLEntrypoint(ref="loop_start"),
        actions=[
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
            ),
            ActionStatement(
                ref="body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["body"],
                args={
                    "condition": "${{ ACTIONS.loop_start.result.iteration < 2 }}",
                    "max_iterations": 10,
                },
            ),
            ActionStatement(
                ref="after_loop",
                action="core.transform.reshape",
                depends_on=["loop_end"],
                args={"value": "${{ ACTIONS.loop_end.result.continue }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert await action_result(result, "loop_start") == {"iteration": 2}
    assert await action_result(result, "body") == 2
    assert await action_result(result, "loop_end") == {"continue": False}
    assert await action_result(result, "after_loop") is False


@pytest.mark.anyio
@pytest.mark.integration
async def test_loop_respects_max_iterations_guard(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_loop_respects_max_iterations_guard.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Loop max iteration guard",
        description="Loop should fail when max_iterations is exceeded",
        entrypoint=DSLEntrypoint(ref="loop_start"),
        actions=[
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
            ),
            ActionStatement(
                ref="body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={"value": 1},
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["body"],
                args={"condition": "${{ True }}", "max_iterations": 3},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        with pytest.raises(WorkflowFailureError) as exc_info:
            await temporal_client.execute_workflow(
                DSLWorkflow.run,
                run_args,
                id=wf_exec_id,
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            )

    assert str(exc_info.value) == "Workflow execution failed"
    cause = exc_info.value.cause
    assert isinstance(cause, ApplicationError)
    assert "exceeded max_iterations=3" in str(cause)


@pytest.mark.anyio
@pytest.mark.integration
async def test_nested_loops_execute_with_independent_closers(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_nested_loops_execute_with_independent_closers.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Nested loops execute",
        description="Nested loop start/end regions should execute and settle correctly",
        entrypoint=DSLEntrypoint(ref="outer_start"),
        actions=[
            ActionStatement(
                ref="outer_start",
                action="core.loop.start",
            ),
            ActionStatement(
                ref="inner_start",
                action="core.loop.start",
                depends_on=["outer_start"],
            ),
            ActionStatement(
                ref="inner_body",
                action="core.transform.reshape",
                depends_on=["inner_start"],
                args={"value": "${{ ACTIONS.inner_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="inner_end",
                action="core.loop.end",
                depends_on=["inner_body"],
                args={"condition": "${{ ACTIONS.inner_start.result.iteration < 1 }}"},
            ),
            ActionStatement(
                ref="outer_body",
                action="core.transform.reshape",
                depends_on=["inner_end"],
                args={"value": "${{ ACTIONS.outer_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="outer_end",
                action="core.loop.end",
                depends_on=["outer_body"],
                args={"condition": "${{ ACTIONS.outer_start.result.iteration < 1 }}"},
            ),
            ActionStatement(
                ref="after",
                action="core.transform.reshape",
                depends_on=["outer_end"],
                args={"value": "${{ ACTIONS.outer_body.result }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert await action_result(result, "outer_start") == {"iteration": 1}
    assert await action_result(result, "inner_start") == {"iteration": 1}
    assert await action_result(result, "inner_body") == 1
    assert await action_result(result, "inner_end") == {"continue": False}
    assert await action_result(result, "outer_body") == 1
    assert await action_result(result, "outer_end") == {"continue": False}
    assert await action_result(result, "after") == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_loop_inside_scatter_gathers_from_loop_scope_exit(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_loop_inside_scatter_gathers_from_loop_scope_exit.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Loop in scatter with gather",
        description=(
            "Loop actions can run inside each scatter stream and gather can consume "
            "results from the loop scope exit."
        ),
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args={"collection": "${{ [3, 5] }}"},
            ),
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
                depends_on=["scatter"],
            ),
            ActionStatement(
                ref="loop_body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={
                    "value": (
                        "${{ ACTIONS.scatter.result + "
                        "ACTIONS.loop_start.result.iteration }}"
                    )
                },
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["loop_body"],
                args={"condition": "${{ ACTIONS.loop_start.result.iteration < 1 }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["loop_end"],
                args={"items": "${{ ACTIONS.loop_end.result.continue }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert await action_result(result, "gather") == [False, False]


@pytest.mark.anyio
@pytest.mark.integration
async def test_loop_run_if_skip_retains_previous_value(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_loop_run_if_skip_retains_previous_value.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Loop run_if skip keeps previous value",
        description=(
            "If a loop-body action runs in one iteration and is skipped in the next, "
            "the previous result is retained until a later successful overwrite."
        ),
        entrypoint=DSLEntrypoint(ref="loop_start"),
        actions=[
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
            ),
            ActionStatement(
                ref="body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="maybe_even",
                action="core.transform.reshape",
                depends_on=["body"],
                run_if="${{ FN.mod(ACTIONS.loop_start.result.iteration, 2) == 0 }}",
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["maybe_even"],
                args={
                    "condition": "${{ ACTIONS.loop_start.result.iteration < 1 }}",
                    "max_iterations": 10,
                },
            ),
            ActionStatement(
                ref="after",
                action="core.transform.reshape",
                depends_on=["loop_end"],
                args={"value": "${{ ACTIONS.loop_end.result.continue }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert await action_result(result, "loop_start") == {"iteration": 1}
    assert await action_result(result, "body") == 1
    assert await action_result(result, "loop_end") == {"continue": False}
    assert await action_result(result, "after") is False
    assert await action_result(result, "maybe_even") == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_skip_outside_loop_bypasses_whole_loop_region(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_skip_outside_loop_bypasses_whole_loop_region.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Outside skip bypasses loop",
        description=(
            "If skip propagation reaches loop_start, the loop body is bypassed and "
            "execution continues after loop_end."
        ),
        entrypoint=DSLEntrypoint(ref="seed"),
        actions=[
            ActionStatement(
                ref="seed",
                action="core.transform.reshape",
                run_if="${{ False }}",
                args={"value": 1},
            ),
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
                depends_on=["seed"],
            ),
            ActionStatement(
                ref="body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["body"],
                args={"condition": "${{ True }}", "max_iterations": 10},
            ),
            ActionStatement(
                ref="after",
                action="core.transform.reshape",
                depends_on=["loop_end"],
                args={"value": "${{ ACTIONS.loop_end.result.continue }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert "loop_start" not in result["ACTIONS"]
    assert "body" not in result["ACTIONS"]
    assert await action_result(result, "loop_end") == {"continue": False}
    assert await action_result(result, "after") is False


@pytest.mark.anyio
@pytest.mark.integration
async def test_skip_inside_loop_does_not_bypass_loop_when_other_dependency_succeeds(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = f"{test_skip_inside_loop_does_not_bypass_loop_when_other_dependency_succeeds.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Inside skip does not bypass loop",
        description=(
            "A skipped action inside the loop should not bypass loop_end when another "
            "dependency still succeeds."
        ),
        entrypoint=DSLEntrypoint(ref="loop_start"),
        actions=[
            ActionStatement(
                ref="loop_start",
                action="core.loop.start",
            ),
            ActionStatement(
                ref="body",
                action="core.transform.reshape",
                depends_on=["loop_start"],
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="maybe_even",
                action="core.transform.reshape",
                depends_on=["body"],
                run_if="${{ ACTIONS.loop_start.result.iteration == 0 }}",
                args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
            ),
            ActionStatement(
                ref="loop_end",
                action="core.loop.end",
                depends_on=["body", "maybe_even"],
                join_strategy=JoinStrategy.ANY,
                args={
                    "condition": "${{ ACTIONS.body.result < 2 }}",
                    "max_iterations": 10,
                },
            ),
            ActionStatement(
                ref="after",
                action="core.transform.reshape",
                depends_on=["loop_end"],
                args={"value": "${{ ACTIONS.loop_end.result.continue }}"},
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    result = await to_data(result)
    assert await action_result(result, "loop_start") == {"iteration": 2}
    assert await action_result(result, "body") == 2
    assert await action_result(result, "maybe_even") == 0
    assert await action_result(result, "loop_end") == {"continue": False}
    assert await action_result(result, "after") is False
