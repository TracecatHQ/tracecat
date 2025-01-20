"""Unit tests for workflows.

Objectives
----------
1. Test that the workflows can be executed in an isolated environment
2. Test that the workflows can be executed with the correct inputs and outputs
3. Test that the workflows can be executed with the correct ordering

"""

import asyncio
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from pydantic import SecretStr
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Workflow
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import LoopStrategy
from tracecat.dsl.models import (
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    RunActionInput,
)
from tracecat.dsl.worker import get_activities, new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow, retry_policies
from tracecat.expressions.common import ExprContext
from tracecat.identifiers.workflow import WorkflowExecutionID, WorkflowID
from tracecat.logger import logger
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.workflow.executions.enums import WorkflowEventType
from tracecat.workflow.executions.models import (
    EventGroup,
    WorkflowExecutionEvent,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.models import WorkflowUpdate


@pytest.fixture(scope="module")
def ray_cluster():
    import ray

    try:
        ray.init()
        yield
    finally:
        ray.shutdown()


@pytest.fixture(scope="module")
def hotfix_local_api_url(monkeysession: pytest.MonkeyPatch):
    """Hotfix to allow workflow tests to run locally.

    We need to set the internal API url to the public API url
    otherwise the tests will fail because it cannot reach the internal API.
    """
    if os.environ.get("GITHUB_ACTIONS") is not None:
        pytest.skip("Skip if running in GitHub Actions")
    monkeysession.setattr(config, "TRACECAT__API_URL", "http://localhost/api")


@pytest.fixture
def dsl(request: pytest.FixtureRequest) -> DSLInput:
    test_name = request.param
    data_path = Path("tests/data/workflows") / f"{test_name}.yml"
    dsl = DSLInput.from_yaml(data_path)
    return dsl


# Fixture to load yaml files from name
@pytest.fixture
def expected(request: pytest.FixtureRequest) -> dict[str, Any]:
    path: Path = request.param
    with path.open() as f:
        yaml_data = f.read()
    data = yaml.safe_load(yaml_data)
    return {key: (value or {}) for key, value in data.items()}


@pytest.fixture
def dsl_with_expected(
    request: pytest.FixtureRequest,
) -> tuple[DSLInput, dict[str, Any]]:
    test_name = request.param
    data_path = Path("tests/data/workflows") / f"{test_name}.yml"
    expected_path = Path("tests/data/workflows") / f"{test_name}_expected.yml"
    dsl = DSLInput.from_yaml(data_path)
    expected = load_expected_dsl_output(expected_path)
    return dsl, expected


def load_expected_dsl_output(path: Path) -> dict[str, Any]:
    with path.open() as f:
        yaml_data = f.read()
    data = yaml.safe_load(yaml_data)
    return {key: (value or {}) for key, value in data.items()}


@pytest.fixture
def runtime_config() -> DSLConfig:
    config = DSLConfig(environment="default")
    logger.info(f"Runtime config: {config}")
    return config


@pytest.mark.parametrize(
    "dsl",
    ["shared_adder_tree", "shared_kite", "shared_tree"],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.anyio
async def test_workflow_can_run_from_yaml(dsl, test_role, temporal_client):
    test_name = f"test_workflow_can_run_from_yaml-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    # Run workflow
    async with Worker(
        temporal_client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    assert len(result[ExprContext.ACTIONS]) == len(dsl.actions)


def assert_respectful_exec_order(dsl: DSLInput, final_context: ExecutionContext):
    act_outputs = final_context[ExprContext.ACTIONS]
    for action in dsl.actions:
        target = action.ref
        for source in action.depends_on:
            source_order = act_outputs[source]["result"]  # type: ignore
            target_order = act_outputs[target]["result"]  # type: ignore
            assert source_order < target_order


@pytest.mark.parametrize(
    "dsl",
    ["unit_ordering_kite", "unit_ordering_kite2"],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.anyio
async def test_workflow_ordering_is_correct(dsl, test_role, temporal_client):
    """We need to test that the ordering of the workflow tasks is correct."""

    # Connect client
    test_name = f"test_workflow_ordering_is_correct-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Run a worker for the activities and workflow
    async with Worker(
        temporal_client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    # Iterate over the actual ordering of the tasks
    # and compare that in the topological ordering every LHS task in a pair executed before the RHS task

    # Check that the execution order respects the graph edges
    assert_respectful_exec_order(dsl, result)


@pytest.mark.parametrize(
    "dsl_with_expected",
    [
        "unit_conditional_adder_tree_skips",
        "unit_conditional_adder_tree_continues",
        "unit_conditional_adder_tree_skip_propagates",
        "unit_conditional_adder_diamond_skip_with_join_weak_dep",
        "unit_transform_reshape_loop",
        "unit_transform_reshape_loop_chained",
        "unit_transform_reshape_arrange",
        "unit_transform_reshape_arrange_loop",
        "unit_transform_reshape_zip",
        "unit_transform_reshape_map_loop",
        "unit_transform_filter_dict",
        "unit_transform_filter_function",
    ],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.anyio
async def test_workflow_completes_and_correct(
    dsl_with_expected, test_role, runtime_config
):
    dsl, expected = dsl_with_expected
    test_name = f"test_correctness_execution-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=ctx_role.get(),
                wf_id=TEST_WF_ID,
                runtime_config=runtime_config,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.types.exceptions.TracecatExpressionError"
                    "TracecatValidationError"
                ],
            ),
        )
    assert result == expected


@pytest.mark.parametrize("dsl", ["stress_adder_tree"], indirect=True, ids=lambda x: x)
@pytest.mark.slow
@pytest.mark.anyio
async def test_stress_workflow(dsl, test_role):
    """Test that we can have multiple executions of the same workflow running at the same time."""
    test_name = f"test_stress_workflow-{dsl.title}"
    client = await get_temporal_client()

    tasks: list[asyncio.Task] = []
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        async with asyncio.TaskGroup() as tg:
            # We can have multiple executions of the same workflow running at the same time
            for i in range(100):
                wf_exec_id = generate_test_exec_id(test_name + f"-{i}")
                task = tg.create_task(
                    client.execute_workflow(
                        DSLWorkflow.run,
                        DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
                        id=wf_exec_id,
                        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                )
                tasks.append(task)

    assert all(task.done() for task in tasks)


@pytest.mark.skip(reason="This test is too slow to run on CI, and breaking atm.")
@pytest.mark.parametrize("runs", [10, 100])
@pytest.mark.slow
@pytest.mark.anyio
async def test_stress_workflow_correctness(runs, test_role, temporal_client):
    """Test that we can have multiple executions of the same workflow running at the same time."""
    test_name = test_stress_workflow_correctness.__name__
    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ TRIGGER.num }}",
                    },
                    "depends_on": [],
                },
                {
                    "ref": "b",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ACTIONS.a.result * 2 }}",
                    },
                    "depends_on": ["a"],
                },
                {
                    "ref": "c",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ACTIONS.b.result * 2 }}",
                    },
                    "depends_on": ["b"],
                },
                {
                    "ref": "d",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ACTIONS.c.result * 2 }}",
                    },
                    "depends_on": ["c"],
                },
            ],
            "description": "Stress testing",
            "inputs": {},
            "returns": "${{ ACTIONS.d.result }}",
            "tests": [],
            "title": f"{test_name}",
            "triggers": [],
            # When the environment is set in the config, it should override the default
            "config": {"environment": "__TEST_ENVIRONMENT__"},
        }
    )

    async with Worker(
        temporal_client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
        max_concurrent_activities=1000,
        max_concurrent_workflow_tasks=1000,
    ):
        async with GatheringTaskGroup() as tg:
            # We can have multiple executions of the same workflow running at the same time
            for i in range(runs):
                wf_exec_id = generate_test_exec_id(test_name + f"-{i}")
                run_args = DSLRunArgs(
                    dsl=dsl,
                    role=ctx_role.get(),
                    wf_id=TEST_WF_ID,
                    trigger_inputs={"num": i},
                )
                tg.create_task(
                    temporal_client.execute_workflow(
                        DSLWorkflow.run,
                        run_args,
                        id=wf_exec_id,
                        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                )

    results = tg.results()
    assert len(results) == runs
    assert list(results) == [i * (2**3) for i in range(runs)]


@pytest.mark.anyio
async def test_workflow_set_environment_correct(test_role, temporal_client):
    test_name = f"{test_workflow_set_environment_correct.__name__}"
    test_description = (
        "Test that we can set the runtime environment for a workflow."
        " The workflow should use the environment set in the DSL config."
    )
    wf_exec_id = generate_test_exec_id(test_name)
    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}",
            "triggers": [],
            # When the environment is set in the config, it should override the default
            "config": {"environment": "__TEST_ENVIRONMENT__"},
        }
    )

    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
        # Note that run args are not set here
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=retry_policies["workflow:fail_fast"],
        )
    assert result == "__TEST_ENVIRONMENT__"


@pytest.mark.anyio
async def test_workflow_override_environment_correct(test_role, temporal_client):
    test_name = f"{test_workflow_override_environment_correct.__name__}"
    test_description = (
        "Test that we can override the runtime environment for a workflow from its run_args."
        " The workflow should use the environment passed in the run_args."
    )
    wf_exec_id = generate_test_exec_id(test_name)
    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}",
            "triggers": [],
            # When the environment is set in the config, it should override the default
            "config": {"environment": "__WRONG_ENVIRONMENT__"},
        }
    )

    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
        # When the environment is set in the run_args, it should override the config
        runtime_config=DSLConfig(environment="__CORRECT_ENVIRONMENT__"),
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=retry_policies["workflow:fail_fast"],
        )
    assert result == "__CORRECT_ENVIRONMENT__"


@pytest.mark.anyio
async def test_workflow_default_environment_correct(test_role, temporal_client):
    test_name = f"{test_workflow_default_environment_correct.__name__}"
    test_description = (
        "Test that we can set the default runtime environment for a workflow."
        " The workflow should use the default runtime environment 'default'."
    )
    wf_exec_id = generate_test_exec_id(test_name)
    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}",
            "triggers": [],
            # When the environment isn't set, it should just be "default"
        }
    )

    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
        # Note that run args are not set here
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=retry_policies["workflow:fail_fast"],
        )
    assert result == "default"


"""Child workflow"""


async def _create_and_commit_workflow(
    dsl: DSLInput, role: Role, *, alias: str | None = None
) -> Workflow:
    async with get_async_session_context_manager() as session:
        # Create the child workflow
        mgmt_service = WorkflowsManagementService(session, role=role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        if not workflow:
            return pytest.fail("Workflow wasn't created")
        if alias:
            await mgmt_service.update_workflow(workflow.id, WorkflowUpdate(alias=alias))
        constructed_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Commit the child workflow
        defn_service = WorkflowDefinitionsService(session, role=role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow.id, dsl=constructed_dsl
        )
        return workflow


async def _run_workflow(client: Client, wf_exec_id: str, run_args: DSLRunArgs):
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with Worker(
        client,
        task_queue=queue,
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=retry_policies["workflow:fail_fast"],
        )
    return result


@pytest.mark.anyio
async def test_child_workflow_success(test_role, temporal_client):
    test_name = f"{test_child_workflow_success.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)
    # Child
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ TRIGGER.number + 1000 }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": "Test child workflow success",
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": "Test child workflow success",
            "triggers": [],
        }
    )
    logger.info("child dsl", child_dsl=child_dsl)

    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)
    # Parent
    parent_dsl = DSLInput(
        **{
            "title": "Parent",
            "description": "Test parent workflow can call child correctly",
            "entrypoint": {
                "ref": "parent",
            },
            "actions": [
                {
                    "ref": "parent",
                    "action": "core.workflow.execute",
                    "for_each": "${{ for var.x in INPUTS.data }}",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {
                            "number": "${{ var.x }}",  # This is the parent's trigger data
                        },
                    },
                    "depends_on": [],
                },
            ],
            "inputs": {
                "data": [1, 2, 3, 4, 5, 6, 7],
            },
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )
    logger.info("parent dsl", parent_dsl=parent_dsl)
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    result = await _run_workflow(temporal_client, wf_exec_id, run_args)

    expected = {
        "ACTIONS": {
            "parent": {
                "result": [1001, 1002, 1003, 1004, 1005, 1006, 1007],
                "result_typename": "list",
            }
        },
        "INPUTS": {"data": [1, 2, 3, 4, 5, 6, 7]},
        "TRIGGER": {},
    }
    assert result == expected


@pytest.mark.anyio
async def test_child_workflow_context_passing(test_role, temporal_client):
    # Setup
    test_name = f"{test_child_workflow_context_passing.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Child
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "reshape_parent_data"},
            "actions": [
                {
                    "ref": "reshape_parent_data",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "parent_data": "${{ TRIGGER.data_from_parent }}",
                        },
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": "Testing child workflow",
            "inputs": {},
            "returns": None,
            "tests": [],
            "title": "Aug 16, 2024, 13:44:37",
            "triggers": [],
        }
    )

    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)

    # Parent
    parent_workflow_id = "wf-00000000000000000000000000000002"
    parent_dsl = DSLInput(
        **{
            "title": "Parent",
            "description": "Test parent workflow can pass context to child",
            "entrypoint": {
                "ref": "parent_first_action",
            },
            "actions": [
                {
                    "ref": "parent_first_action",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "reshaped_data": "${{ TRIGGER.data }}",
                        },
                    },
                    "depends_on": [],
                    "description": "",
                },
                {
                    "ref": "parent_second_action",
                    "action": "core.workflow.execute",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {
                            "data_from_parent": "Parent sent child ${{ ACTIONS.parent_first_action.result.reshaped_data }}",  # This is the parent's trigger data
                        },
                    },
                    "depends_on": ["parent_first_action"],
                    "description": "",
                },
            ],
            "inputs": {},
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=parent_workflow_id,
        trigger_inputs={
            "data": "__EXPECTED_DATA__",
        },
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    # Parent expected
    expected = {
        "ACTIONS": {
            "parent_first_action": {
                "result": {
                    "reshaped_data": "__EXPECTED_DATA__",
                },
                "result_typename": "dict",
            },
            "parent_second_action": {
                "result": {
                    "ACTIONS": {
                        "reshape_parent_data": {
                            "result": {
                                "parent_data": "Parent sent child __EXPECTED_DATA__"
                            },
                            "result_typename": "dict",
                        }
                    },
                    "INPUTS": {},
                    "TRIGGER": {
                        "data_from_parent": "Parent sent child __EXPECTED_DATA__"
                    },
                },
                "result_typename": "dict",
            },
        },
        "INPUTS": {},
        "TRIGGER": {"data": "__EXPECTED_DATA__"},
    }
    assert result == expected


@pytest.fixture
def child_dsl():
    child_dsl = DSLInput(
        entrypoint=DSLEntrypoint(expects={}, ref="reshape"),
        actions=[
            ActionStatement(
                ref="reshape",
                action="core.transform.reshape",
                args={
                    "value": {
                        "data": "${{ TRIGGER.data }}",
                        "index": "${{ TRIGGER.index }}",
                    },
                },
                depends_on=[],
                description="",
                run_if=None,
                for_each=None,
            )
        ],
        description="Testing child workflow",
        inputs={},
        returns="${{ ACTIONS.reshape.result }}",
        title="Child",
        triggers=[],
    )
    return child_dsl


@pytest.mark.parametrize(
    "loop_strategy,loop_kwargs",
    [
        pytest.param(LoopStrategy.PARALLEL, {}, id="parallel"),
        pytest.param(LoopStrategy.SEQUENTIAL, {}, id="sequential"),
        pytest.param(LoopStrategy.BATCH, {"batch_size": 2}, id="batch"),
    ],
)
@pytest.mark.anyio
async def test_child_workflow_loop(
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    loop_strategy: LoopStrategy,
    loop_kwargs: dict[str, Any],
):
    # Setup
    test_name = test_child_workflow_loop.__name__
    wf_exec_id = generate_test_exec_id(test_name)

    # Child

    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)

    # Parent
    parent_dsl = DSLInput(
        title="Parent",
        description="Test parent workflow can pass context to child",
        entrypoint=DSLEntrypoint(ref="run_child", expects={}),
        actions=[
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={
                    "workflow_id": child_workflow.id,
                    "trigger_inputs": {
                        "data": "Parent sent child ${{ TRIGGER.data }}",  # This is the parent's trigger data
                        "index": "${{ var.x }}",
                    },
                    "loop_strategy": loop_strategy.value,
                    **loop_kwargs,
                },
                depends_on=[],
                description="",
                for_each="${{ for var.x in FN.range(0, 5) }}",
                run_if=None,
            ),
        ],
        inputs={},
        returns=None,
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id="wf-00000000000000000000000000000002",
        trigger_inputs={
            "data": "__EXPECTED_DATA__",
        },
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    # Parent expected
    expected = {
        "ACTIONS": {
            "run_child": {
                "result": [
                    {
                        "index": 0,
                        "data": "Parent sent child __EXPECTED_DATA__",
                    },
                    {
                        "index": 1,
                        "data": "Parent sent child __EXPECTED_DATA__",
                    },
                    {
                        "index": 2,
                        "data": "Parent sent child __EXPECTED_DATA__",
                    },
                    {
                        "index": 3,
                        "data": "Parent sent child __EXPECTED_DATA__",
                    },
                    {
                        "index": 4,
                        "data": "Parent sent child __EXPECTED_DATA__",
                    },
                ],
                "result_typename": "list",
            },
        },
        "INPUTS": {},
        "TRIGGER": {"data": "__EXPECTED_DATA__"},
    }
    assert result == expected


# Test workflow alias
@pytest.mark.anyio
async def test_single_child_workflow_alias(
    test_role: Role, temporal_client: Client, child_dsl: DSLInput
):
    test_name = test_single_child_workflow_alias.__name__
    wf_exec_id = generate_test_exec_id(test_name)
    child_workflow = await _create_and_commit_workflow(
        child_dsl, test_role, alias="the_child"
    )

    assert child_workflow.alias == "the_child"

    parent_dsl = DSLInput(
        title="Parent",
        description="Test parent workflow can pass context to child",
        entrypoint=DSLEntrypoint(ref="run_child", expects={}),
        actions=[
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={
                    "workflow_alias": "the_child",
                    "trigger_inputs": {
                        "data": "Test",
                        "index": 0,
                    },
                },
                depends_on=[],
                description="",
                for_each=None,
                run_if=None,
            ),
        ],
        inputs={},
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id="wf-00000000000000000000000000000002",
    )
    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    # Parent expected
    assert result == {"data": "Test", "index": 0}


@pytest.mark.parametrize(
    "alias,loop_strategy,loop_kwargs",
    [
        pytest.param(
            "basic_alias",
            LoopStrategy.PARALLEL,
            {},
            id="basic_alias",
        ),
        pytest.param(
            "sequential_child",
            LoopStrategy.SEQUENTIAL,
            {},
            id="sequential_alias",
        ),
        pytest.param(
            "batch_child",
            LoopStrategy.BATCH,
            {"batch_size": 2},
            id="batch_size_2",
        ),
    ],
)
@pytest.mark.anyio
async def test_child_workflow_alias_with_loop(
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    alias: str,
    loop_strategy: LoopStrategy,
    loop_kwargs: dict[str, Any],
):
    """Test that child workflows can be executed using aliases."""
    test_name = test_single_child_workflow_alias.__name__
    wf_exec_id = generate_test_exec_id(test_name)
    child_workflow = await _create_and_commit_workflow(
        child_dsl, test_role, alias=alias
    )

    assert child_workflow.alias == alias

    parent_dsl = DSLInput(
        title="Parent",
        description="Test parent workflow can pass context to child",
        entrypoint=DSLEntrypoint(ref="run_child", expects={}),
        actions=[
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={
                    "workflow_alias": alias,
                    "trigger_inputs": {
                        "data": "Parent sent child ${{ TRIGGER }}",  # This is the parent's trigger data
                        "index": "${{ var.x }}",
                    },
                    "loop_strategy": loop_strategy.value,
                    **loop_kwargs,
                },
                depends_on=[],
                description="",
                for_each="${{ for var.x in FN.range(0, 5) }}",
                run_if=None,
            ),
        ],
        inputs={},
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id="wf-00000000000000000000000000000002",
        trigger_inputs="__EXPECTED_DATA__",
    )
    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    # Parent expected
    assert result == [
        {
            "index": 0,
            "data": "Parent sent child __EXPECTED_DATA__",
        },
        {
            "index": 1,
            "data": "Parent sent child __EXPECTED_DATA__",
        },
        {
            "index": 2,
            "data": "Parent sent child __EXPECTED_DATA__",
        },
        {
            "index": 3,
            "data": "Parent sent child __EXPECTED_DATA__",
        },
        {
            "index": 4,
            "data": "Parent sent child __EXPECTED_DATA__",
        },
    ]


@pytest.mark.anyio
async def test_single_child_workflow_override_environment_correct(
    test_role, temporal_client
):
    test_name = f"{test_single_child_workflow_override_environment_correct.__name__}"
    test_description = (
        "Test that we can set the runtime environment for a single child workflow"
    )
    wf_exec_id = generate_test_exec_id(test_name)
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment}}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}:child",
            "triggers": [],
        }
    )
    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)

    parent_dsl = DSLInput(
        **{
            "title": f"{test_name}:parent",
            "description": test_description,
            "entrypoint": {
                "ref": "parent",
            },
            "actions": [
                {
                    "ref": "parent",
                    "action": "core.workflow.execute",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {},
                        "environment": "__TEST_ENVIRONMENT__",
                    },
                    "depends_on": [],
                },
            ],
            "inputs": {},
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    expected = {
        "ACTIONS": {
            "parent": {
                "result": "__TEST_ENVIRONMENT__",
                "result_typename": "str",
            }
        },
        "INPUTS": {},
        "TRIGGER": {},
    }
    assert result == expected


@pytest.mark.anyio
async def test_multiple_child_workflow_override_environment_correct(
    test_role, temporal_client
):
    test_name = f"{test_multiple_child_workflow_override_environment_correct.__name__}"
    test_description = (
        "Test that we can set the runtime environment for multiple child workflows"
    )
    wf_exec_id = generate_test_exec_id(test_name)
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}:child",
            "description": test_description,
            "triggers": [],
        }
    )
    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)
    parent_dsl = DSLInput(
        **{
            "title": f"{test_name}:parent",
            "description": test_description,
            "entrypoint": {
                "ref": "parent",
            },
            "actions": [
                {
                    "ref": "parent",
                    "action": "core.workflow.execute",
                    "for_each": "${{ for var.env in ['prod', 'dev', 'staging', 'client1', 'client2'] }}",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {},
                        "environment": "${{ var.env }}",
                    },
                    "depends_on": [],
                },
            ],
            "inputs": {},
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    expected = {
        "ACTIONS": {
            "parent": {
                "result": ["prod", "dev", "staging", "client1", "client2"],
                "result_typename": "list",
            }
        },
        "INPUTS": {},
        "TRIGGER": {},
    }
    assert result == expected


@pytest.mark.anyio
async def test_single_child_workflow_environment_has_correct_default(
    test_role, temporal_client
):
    test_name = f"{test_single_child_workflow_environment_has_correct_default.__name__}"
    test_description = (
        "Test that the default environment is set correctly for a single child workflow"
    )
    wf_exec_id = generate_test_exec_id(test_name)
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "config": {"environment": "__TESTING_DEFAULT__"},
            "tests": [],
            "title": f"{test_name}:child",
            "triggers": [],
        }
    )
    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)
    parent_dsl = DSLInput(
        **{
            "title": f"{test_name}:parent",
            "description": test_description,
            "entrypoint": {
                "ref": "parent",
            },
            "actions": [
                {
                    "ref": "parent",
                    "action": "core.workflow.execute",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {},
                        # No environment set, should default to the child DSL default
                    },
                    "depends_on": [],
                },
            ],
            "inputs": {},
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    expected = {
        "ACTIONS": {
            "parent": {
                "result": "__TESTING_DEFAULT__",
                "result_typename": "str",
            }
        },
        "INPUTS": {},
        "TRIGGER": {},
    }
    assert result == expected


@pytest.mark.anyio
async def test_multiple_child_workflow_environments_have_correct_defaults(
    test_role, temporal_client
):
    test_name = (
        f"{test_multiple_child_workflow_environments_have_correct_defaults.__name__}"
    )
    test_description = "Test that the default environment is set correctly for multiple child workflows"
    wf_exec_id = generate_test_exec_id(test_name)
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ENV.environment }} ${{ TRIGGER.number }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "config": {
                "environment": "__TESTING_DEFAULT__"
            },  # We won't pass environmemt in the parent, so this should be the default
            "tests": [],
            "title": f"{test_name}:child",
            "triggers": [],
        }
    )
    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)

    parent_dsl = DSLInput(
        **{
            "title": f"{test_name}:parent",
            "description": test_description,
            "entrypoint": {
                "ref": "parent",
            },
            "actions": [
                {
                    "ref": "parent",
                    "action": "core.workflow.execute",
                    "for_each": "${{ for var.number in [1, 2, 3] }}",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {
                            "number": "${{ var.number }}",
                        },
                        # No environment set, should default to the child DSL default
                    },
                    "depends_on": [],
                },
            ],
            "inputs": {},
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    expected = {
        "ACTIONS": {
            "parent": {
                "result": [
                    "__TESTING_DEFAULT__ 1",
                    "__TESTING_DEFAULT__ 2",
                    "__TESTING_DEFAULT__ 3",
                ],
                "result_typename": "list",
            }
        },
        "INPUTS": {},
        "TRIGGER": {},
    }
    assert result == expected


@pytest.mark.anyio
async def test_single_child_workflow_get_correct_secret_environment(
    test_role, temporal_client
):
    # We need to set this on the API server, as we run it in a separate process
    # monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)
    test_name = f"{test_single_child_workflow_get_correct_secret_environment.__name__}"
    test_description = "Test that a single child workflow can get a secret from the correect environment"

    # Add secrets to the db
    async with SecretsService.with_session(role=test_role) as service:
        await service.create_secret(
            SecretCreate(
                name="test_single_child_workflow_get_correct_secret_environment",
                environment="__FIRST__",
                keys=[SecretKeyValue(key="KEY", value=SecretStr("FIRST_VALUE"))],
            )
        )
        await service.create_secret(
            SecretCreate(
                name="test_single_child_workflow_get_correct_secret_environment",
                environment="__SECOND__",
                keys=[SecretKeyValue(key="KEY", value=SecretStr("SECOND_VALUE"))],
            )
        )

    wf_exec_id = generate_test_exec_id(test_name)
    child_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "KEY is ${{ SECRETS.test_single_child_workflow_get_correct_secret_environment.KEY }}",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": test_description,
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "config": {
                "environment": "__TESTING_DEFAULT__"
            },  # Default child environment
            "tests": [],
            "title": f"{test_name}:child",
            "triggers": [],
        }
    )
    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)
    parent_dsl = DSLInput(
        **{
            "title": f"{test_name}:parent",
            "description": test_description,
            "entrypoint": {
                "ref": "parent",
            },
            "actions": [
                {
                    "ref": "parent",
                    "action": "core.workflow.execute",
                    "for_each": "${{ for var.env in ['__FIRST__', '__SECOND__'] }}",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {},
                        "environment": "${{ var.env }}",
                    },
                    "depends_on": [],
                },
            ],
            "inputs": {},
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    result = await _run_workflow(temporal_client, wf_exec_id, run_args)
    expected = {
        "ACTIONS": {
            "parent": {
                "result": [
                    "KEY is FIRST_VALUE",
                    "KEY is SECOND_VALUE",
                ],
                "result_typename": "list",
            }
        },
        "INPUTS": {},
        "TRIGGER": {},
    }
    assert result == expected


@pytest.mark.anyio
async def test_pull_based_workflow_fetches_latest_version(temporal_client, test_role):
    """Test that a pull-based workflow fetches the latest version after being updated.

    Steps
    -----
    1. Create workflow
    2. Create worfklow definition 1
    3. Run the workflow as pull based, check the result
    4. Create workflow definition 2
    5. Run the workflow again as pull based, check the result
    """
    test_name = f"{test_pull_based_workflow_fetches_latest_version.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)
    first_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "__EXPECTED_FIRST_RESULT__",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": "Test that a pull-based workflow fetches the latest version",
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}:first",
            "triggers": [],
        }
    )
    async with get_async_session_context_manager() as session:
        # 1) Create the workflow
        mgmt_service = WorkflowsManagementService(session, role=test_role)
        res = await mgmt_service.create_workflow_from_dsl(
            first_dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        if not workflow:
            return pytest.fail("Workflow wasn't created")
        constructed_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # 2) Create first workflow definition
        defn_service = WorkflowDefinitionsService(session, role=test_role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow.id, dsl=constructed_dsl
        )

    run_args = DSLRunArgs(
        role=test_role,
        wf_id=workflow.id,
        # NOTE: Not setting dsl here to make it pull based
        # Not setting schedule_id here to make it use the passed in trigger inputs
    )
    result = await _run_workflow(temporal_client, f"{wf_exec_id}:first", run_args)

    assert result == "__EXPECTED_FIRST_RESULT__"

    second_dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "__EXPECTED_SECOND_RESULT__",
                    },
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": "Test that a pull-based workflow fetches the latest version",
            "inputs": {},
            "returns": "${{ ACTIONS.a.result }}",
            "tests": [],
            "title": f"{test_name}:second",
            "triggers": [],
        }
    )
    async with get_async_session_context_manager() as session:
        # 4) Create second workflow definition
        defn_service = WorkflowDefinitionsService(session, role=test_role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow.id, dsl=second_dsl
        )

    result = await _run_workflow(temporal_client, f"{wf_exec_id}:second", run_args)
    assert result == "__EXPECTED_SECOND_RESULT__"


# Get the line number dynamically
PARTIAL_DIVISION_BY_ZERO_ERROR = {
    "ref": "start",
    "message": (
        "There was an error in the executor when calling action 'core.transform.reshape' (500).\n"
        "\n"
        "TracecatExpressionError: Error evaluating expression `1/0`\n"
        "\n"
        "[evaluator] Evaluation failed at node:\n"
        "```\n"
        "binary_op\n"
        "  literal\t1\n"
        "  /\n"
        "  literal\t0\n"
        "\n"
        "```\n"
        'Reason: Error trying to process rule "binary_op":\n'
        "\n"
        "Cannot divide by zero\n"
        "\n"
        "------------------------------\n"
        # f"File: /app/{"/".join(run_action_on_ray_cluster.__module__.split('.'))}.py\n"
        # f"Function: {run_action_on_ray_cluster.__name__}\n"
        # f"Line: {run_action_on_ray_cluster.__code__.co_firstlineno}"
    ),
    "type": "ExecutorClientError",
    "expr_context": "ACTIONS",
    "attempt": 1,
}


def approximately_equal(result: Any, expected: Any) -> None:
    assert type(result) is type(expected)

    match result:
        case str():
            assert result == expected or result.startswith(expected)
        case dict():
            for key in result:
                approximately_equal(result[key], expected[key])
        case list():
            for i in range(len(result)):
                approximately_equal(result[i], expected[i])
        case _:
            assert result == expected


def _get_test_id(test_case):
    """Extract test title from test case tuple."""
    match test_case:
        case {"title": title}:
            return title
        case _:
            return None


@pytest.mark.parametrize(
    "dsl_data,expected",
    [
        # Error handling
        (
            {
                "title": "simple_skip_ok_ERR_check_error_info",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "start"},
                "actions": [
                    {
                        "ref": "start",
                        "action": "core.transform.reshape",
                        "args": {"value": "${{ 1/0 }}"},
                    },
                    {
                        "ref": "success_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "SUCCESS"},
                        "depends_on": ["start"],  # SKIPPED because error
                    },
                    {
                        "ref": "error_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "ERROR"},
                        "depends_on": ["start.error"],  # RUNS
                    },
                ],
                "inputs": {},
                "returns": {
                    "start": "${{ ACTIONS.start.result }}",
                    "start.error": "${{ ACTIONS.start.error }}",
                    "success": "${{ ACTIONS.success_path.result }}",
                    "error": "${{ ACTIONS.error_path.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "start": None,
                "start.error": PARTIAL_DIVISION_BY_ZERO_ERROR,
                "success": None,
                "error": "ERROR",
            },
        ),
        (
            # Now, we aren't properly propagating the skipped edge
            {
                "title": "run_if_skips_few_OK",
                "description": "Test that run_if skips work",
                "entrypoint": {"expects": {}, "ref": "a"},
                "actions": [
                    {
                        "ref": "a",
                        "action": "core.transform.reshape",
                        "args": {"value": "A"},
                    },
                    {
                        "ref": "b",
                        "action": "core.transform.reshape",
                        "args": {"value": "B"},
                        "depends_on": ["a"],
                        "run_if": "${{ False }}",
                    },
                    {
                        "ref": "c",
                        "action": "core.transform.reshape",
                        "args": {"value": "C"},
                        "depends_on": ["b"],
                    },
                    {
                        "ref": "d",
                        "action": "core.transform.reshape",
                        "args": {"value": "D"},
                        "depends_on": ["c"],
                    },
                ],
                "inputs": {},
                "returns": {
                    "a": "${{ ACTIONS.a.result }}",
                    "b": "${{ ACTIONS.b.result }}",
                    "c": "${{ ACTIONS.c.result }}",
                    "d": "${{ ACTIONS.d.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "a": "A",
                "b": None,
                "c": None,
                "d": None,
            },
        ),
        (
            {
                "title": "simple_skip_ok_ERR",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "a"},
                "actions": [
                    {
                        "ref": "a",
                        "action": "core.transform.reshape",
                        "args": {"value": "${{ 1/0 }}"},
                    },
                    {
                        "ref": "success_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "SUCCESS"},
                        "depends_on": ["a"],  # SKIPPED because error
                    },
                    {
                        "ref": "error_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "ERROR"},
                        "depends_on": ["a.error"],  # RUNS
                    },
                ],
                "inputs": {},
                "returns": {
                    "success": "${{ ACTIONS.success_path.result }}",
                    "error": "${{ ACTIONS.error_path.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "success": None,
                "error": "ERROR",
            },
        ),
        (
            {
                "title": "simple_skip_error_OK",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "a"},
                "actions": [
                    {
                        "ref": "a",
                        "action": "core.transform.reshape",
                        "args": {"value": "NO_ERROR_HERE"},
                    },
                    {
                        "ref": "success_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "SUCCESS"},
                        "depends_on": ["a"],  # Implicitly `a.success`
                    },
                    {
                        "ref": "error_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "ERROR"},
                        "depends_on": ["a.error"],  # This action should NOT run
                    },
                ],
                "inputs": {},
                "returns": {
                    "success": "${{ ACTIONS.success_path.result }}",
                    "error": "${{ ACTIONS.error_path.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "success": "SUCCESS",
                "error": None,
            },
        ),
        (
            {
                "title": "multiple_errors_skipped_OK",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "start"},
                "actions": [
                    {
                        "ref": "start",
                        "action": "core.transform.reshape",
                        "args": {"value": "NO_ERROR_HERE"},
                    },
                    {
                        "ref": "success_path",
                        "action": "core.transform.reshape",
                        "args": {"value": "SUCCESS"},
                        "depends_on": ["start"],  # Implicitly `start.success`
                    },
                    {
                        "ref": "error_path_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ERROR_1"},
                        "depends_on": ["start.error"],  # This action should NOT run
                    },
                    {
                        "ref": "error_path_2",
                        "action": "core.transform.reshape",
                        "args": {"value": "ERROR_2"},
                        "depends_on": ["error_path_1"],  # This action should NOT run
                    },
                ],
                "inputs": {},
                "returns": {
                    "success_path": "${{ ACTIONS.success_path.result }}",
                    "error_path_1": "${{ ACTIONS.error_path_1.result }}",
                    "error_path_2": "${{ ACTIONS.error_path_2.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "success_path": "SUCCESS",
                "error_path_1": None,
                "error_path_2": None,
            },
        ),
        (
            {  # What's currently wrong here is that we're skipping the error path
                "title": "run_if_skips_one_OK",
                "description": "Test that run_if skips work",
                "entrypoint": {"expects": {}, "ref": "a"},
                "actions": [
                    {
                        "ref": "a",
                        "action": "core.transform.reshape",
                        "args": {"value": "A"},
                    },
                    {
                        "ref": "b",
                        "action": "core.transform.reshape",
                        "args": {"value": "B"},
                        "depends_on": ["a"],
                        "run_if": "${{ False }}",
                    },
                ],
                "inputs": {},
                "returns": {
                    "a": "${{ ACTIONS.a.result }}",
                    "b": "${{ ACTIONS.b.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "a": "A",
                "b": None,
            },
        ),
        (
            {
                "title": "join_on_same_node_OK",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "start"},
                "actions": [
                    {
                        "ref": "start",
                        "action": "core.transform.reshape",
                        "args": {"value": "START"},
                    },
                    {
                        "ref": "join",
                        "action": "core.transform.reshape",
                        "args": {"value": "JOIN"},
                        "depends_on": ["start", "start.error"],
                        "join_strategy": "any",
                    },
                ],
                "inputs": {},
                "returns": {
                    "start": "${{ ACTIONS.start.result }}",
                    "join": "${{ ACTIONS.join.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "start": "START",
                "join": "JOIN",  # This should still run, as we took the success path
            },
        ),
        (
            {
                "title": "diamond_join_success",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "start"},
                "actions": [
                    {
                        "ref": "start",
                        "action": "core.transform.reshape",
                        "args": {"value": "START"},  # RUNS
                    },
                    {
                        "ref": "left",
                        "action": "core.transform.reshape",
                        "args": {"value": "SUCCESS"},
                        "depends_on": ["start"],  # RUNS
                    },
                    {
                        "ref": "right",
                        "action": "core.transform.reshape",
                        "args": {"value": "MAYBE_ERROR"},
                        "depends_on": ["start"],  # RUNS
                    },
                    {
                        "ref": "join",
                        "action": "core.transform.reshape",
                        "args": {"value": "JOIN"},
                        "depends_on": [
                            "left",  # RUNS
                            "right",  # RUNS
                            "right.error",  # SKIPS
                        ],
                        "join_strategy": "any",
                    },
                ],
                "inputs": {},
                "returns": {
                    "start": "${{ ACTIONS.start.result }}",
                    "left": "${{ ACTIONS.left.result }}",
                    "right": "${{ ACTIONS.right.result }}",
                    "join": "${{ ACTIONS.join.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "start": "START",
                "left": "SUCCESS",
                "right": "MAYBE_ERROR",
                "join": "JOIN",
            },
        ),
        (
            {
                "title": "diamond_join_right_error",
                "description": "Test that workflow errors are correctly handled",
                "entrypoint": {"expects": {}, "ref": "start"},
                "actions": [
                    {
                        "ref": "start",
                        "action": "core.transform.reshape",
                        "args": {"value": "START"},  # RUNS
                    },
                    {
                        "ref": "left",
                        "action": "core.transform.reshape",
                        "args": {"value": "SUCCESS"},
                        "depends_on": ["start"],  # RUNS
                    },
                    {
                        "ref": "right",
                        "action": "core.transform.reshape",
                        "args": {"value": "${{ 1/0 }}"},
                        "depends_on": ["start"],  # ERROR
                    },
                    {
                        "ref": "join",
                        "action": "core.transform.reshape",
                        "args": {"value": "JOIN"},
                        "depends_on": [
                            "left",  # RUNS
                            "right",  # RUNS
                            "right.error",  # SKIPS: Absorbs the error path
                        ],
                        "join_strategy": "any",
                    },
                ],
                "inputs": {},
                "returns": {
                    "start": "${{ ACTIONS.start.result }}",
                    "left": "${{ ACTIONS.left.result }}",
                    "right": "${{ ACTIONS.right.result }}",
                    "join": "${{ ACTIONS.join.result }}",
                },
                "tests": [],
                "triggers": [],
            },
            {
                "start": "START",
                "left": "SUCCESS",
                "right": None,
                "join": "JOIN",
            },
        ),
    ],
    ids=_get_test_id,
)
@pytest.mark.anyio
async def test_workflow_error_path(test_role, runtime_config, dsl_data, expected):
    dsl = DSLInput(**dsl_data)
    test_name = f"test_workflow_error-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=test_role,
                wf_id=TEST_WF_ID,
                runtime_config=runtime_config,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            run_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.types.exceptions.TracecatExpressionError"
                    "TracecatValidationError"
                ],
            ),
        )
        approximately_equal(result, expected)


@pytest.mark.anyio
async def test_workflow_join_unreachable(test_role, runtime_config):
    """Test join strategy behavior with unreachable nodes.

    Args:
        dsl_data: The workflow DSL configuration to test
        should_throw: Whether the workflow should raise an error

    The test verifies:
    1. Workflows with satisfied dependencies execute successfully
    2. Workflows with unsatisfied required dependencies fail appropriately
    """
    from temporalio.exceptions import TemporalError

    dsl_data = {
        "title": "join_all_throws",
        "description": "Test that workflow fails when required dependency is skipped",
        "entrypoint": {"expects": {}, "ref": "start"},
        "actions": [
            {
                "ref": "start",
                "action": "core.transform.reshape",
                "args": {"value": "START"},
            },
            {
                "ref": "left",
                "action": "core.transform.reshape",
                "args": {"value": "LEFT"},
                "depends_on": ["start"],
            },
            {
                "ref": "right",
                "action": "core.transform.reshape",
                "args": {"value": "RIGHT"},
                "depends_on": ["start"],
                "run_if": "${{ False }}",  # This causes the error
            },
            {
                "ref": "join",
                "action": "core.transform.reshape",
                "args": {"value": "JOIN"},
                "depends_on": ["left", "right"],
                "join_strategy": "all",  # Fails because 'right' is skipped
            },
        ],
    }
    dsl = DSLInput(**dsl_data)
    test_name = f"test_workflow_join_unreachable-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    client = await get_temporal_client()

    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        with pytest.raises(TemporalError):
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(
                    dsl=dsl,
                    role=test_role,
                    wf_id=TEST_WF_ID,
                    runtime_config=runtime_config,
                ),
                id=wf_exec_id,
                task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                run_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(
                    maximum_attempts=1,
                    non_retryable_error_types=[
                        "tracecat.types.exceptions.TracecatExpressionError",
                        "TracecatValidationError",
                    ],
                ),
            )


@pytest.mark.anyio
async def test_workflow_multiple_entrypoints(test_role, runtime_config):
    """Test workflow behavior with multiple entrypoints.

    Args:
        dsl_data: The workflow DSL configuration to test
        should_throw: Whether the workflow should raise an error

    The test verifies:
    1. Workflows with satisfied dependencies execute successfully
    2. Workflows with unsatisfied required dependencies fail appropriately
    """

    dsl = DSLInput(
        **{
            "title": "multiple_entrypoints",
            "description": "Test that workflow can have multiple entrypoints",
            "entrypoint": {"expects": {}, "ref": "start"},
            "actions": [
                {
                    "ref": "entrypoint_1",
                    "action": "core.transform.reshape",
                    "args": {"value": "ENTRYPOINT_1"},
                },
                {
                    "ref": "entrypoint_2",
                    "action": "core.transform.reshape",
                    "args": {"value": "ENTRYPOINT_2"},
                },
                {
                    "ref": "entrypoint_3",
                    "action": "core.transform.reshape",
                    "args": {"value": "ENTRYPOINT_3"},
                },
                {
                    "ref": "join",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "first": "${{ ACTIONS.entrypoint_1.result }}",
                            "second": "${{ ACTIONS.entrypoint_2.result }}",
                            "third": "${{ ACTIONS.entrypoint_3.result }}",
                        }
                    },
                    "depends_on": ["entrypoint_1", "entrypoint_2", "entrypoint_3"],
                    "join_strategy": "all",
                },
            ],
            "returns": "${{ ACTIONS.join.result }}",
        }
    )
    test_name = f"test_workflow_multiple_entrypoints-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    client = await get_temporal_client()

    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=test_role,
                wf_id=TEST_WF_ID,
                runtime_config=runtime_config,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            run_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.types.exceptions.TracecatExpressionError",
                    "TracecatValidationError",
                ],
            ),
        )
    assert result == {
        "first": "ENTRYPOINT_1",
        "second": "ENTRYPOINT_2",
        "third": "ENTRYPOINT_3",
    }


@pytest.mark.anyio
async def test_workflow_runs_template_for_each(
    test_role,
    runtime_config,
    temporal_client,
    db_session_with_repo,
):
    """Test workflow behavior with for_each.

    Args:
        dsl_data: The workflow DSL configuration to test
        should_throw: Whether the workflow should raise an error

    The test verifies:
    1. Workflows with satisfied dependencies execute successfully
    2. Workflows with unsatisfied required dependencies fail appropriately
    """

    from tracecat.expressions.expectations import ExpectedField
    from tracecat.registry.actions.models import (
        ActionStep,
        RegistryActionCreate,
        TemplateAction,
        TemplateActionDefinition,
    )
    from tracecat.registry.actions.service import RegistryActionsService
    from tracecat.registry.repository import Repository

    # Arrange
    # 1. Register test udfs
    repo = Repository()
    session, db_repo_id = db_session_with_repo

    # It then returns the fetched secret
    action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Action",
            description="This is just a test",
            name="template_action",
            namespace="testing",
            display_group="Testing",
            expects={
                "num": ExpectedField(
                    type="int",
                    description="Number to add 100 to",
                )
            },
            secrets=[],  # NOTE: We have no secrets at the template level
            steps=[
                ActionStep(
                    ref="base",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ inputs.num + 100 }}",
                    },
                )
            ],
            returns="${{ steps.base.result }}",
        ),
    )

    repo.register_template_action(action)

    assert "testing.template_action" in repo

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.template_action"), db_repo_id)
    )

    dsl = DSLInput(
        **{
            "title": "for_each",
            "description": "Test that workflow can have for_each",
            "entrypoint": {"expects": {}, "ref": "start"},
            "actions": [
                {
                    "ref": "entrypoint_1",
                    "action": "core.transform.reshape",
                    "for_each": "${{ for var.x in [1,2,3,4,5] }}",
                    "args": {"value": "${{ var.x + 100 }}"},
                },
            ],
            "returns": "${{ ACTIONS.entrypoint_1.result }}",
        }
    )
    test_name = f"test_workflow_for_each-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    async with Worker(
        temporal_client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=get_activities(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=test_role,
                wf_id=TEST_WF_ID,
                runtime_config=runtime_config,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            run_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.types.exceptions.TracecatExpressionError",
                    "TracecatValidationError",
                ],
            ),
        )
    assert result == [101, 102, 103, 104, 105]


@dataclass
class ErrorHandlerWfAndDslT:
    dsl: DSLInput
    wf: Workflow


@pytest.fixture
async def error_handler_wf_and_dsl(
    test_role: Role,
) -> AsyncGenerator[ErrorHandlerWfAndDslT, None]:
    # Handler dsl
    dsl = DSLInput(
        title="Testing Error Handler",
        description="This is a test error handler",
        entrypoint=DSLEntrypoint(),
        actions=[
            ActionStatement(
                ref="error_handler",
                action="core.transform.reshape",
                args={"value": "ERROR_HANDLER_WAS_CALLED"},
                run_if=None,
                for_each=None,
            ),
        ],
    )
    alias = "testing.error_handler"

    async with get_async_session_context_manager() as session:
        # Create the child workflow
        mgmt_service = WorkflowsManagementService(session, role=test_role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        if not workflow:
            raise ValueError("Workflow wasn't created")
        if alias:
            await mgmt_service.update_workflow(workflow.id, WorkflowUpdate(alias=alias))
        constructed_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Commit the child workflow
        defn_service = WorkflowDefinitionsService(session, role=test_role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow.id, dsl=constructed_dsl
        )
        try:
            yield ErrorHandlerWfAndDslT(dsl, workflow)
        finally:
            await mgmt_service.delete_workflow(workflow.id)


@pytest.fixture
def failing_dsl():
    return DSLInput(
        title="Division by zero",
        description="Test that the error handler can capture errors",
        entrypoint=DSLEntrypoint(),
        actions=[
            ActionStatement(
                ref="failing_action",
                action="core.transform.reshape",
                args={"value": "${{ 1/0 }}"},
                run_if=None,
                for_each=None,
            ),
        ],
    )


def assert_erroneous_task_failed_correctly(
    events: list[WorkflowExecutionEvent],
) -> WorkflowExecutionEvent[RunActionInput]:
    # 4.1 Match the failing task:
    #  - event_type == "ACTIVITY_TASK_FAILED"
    # - Matching action ref
    # - Matching acation input
    evt = next(
        event
        for event in events
        if (
            event.event_type == WorkflowEventType.ACTIVITY_TASK_FAILED
            and event.event_group
            and event.event_group.action_ref == "failing_action"
        )
    )
    assert evt is not None, "No failing task event found"

    # Check event group exists and type
    assert evt.event_group is not None
    assert isinstance(evt.event_group, EventGroup)

    # Check event group attributes
    group = evt.event_group
    assert group.udf_namespace == "core.transform"
    assert group.udf_name == "reshape"
    assert group.udf_key == "core.transform.reshape"
    assert group.action_ref == "failing_action"
    assert group.action_title == "Failing action"

    # Check action input exists and type
    assert isinstance(group.action_input, RunActionInput)

    # Check task exists and type
    assert isinstance(group.action_input.task, ActionStatement)

    # Check task attributes
    task = group.action_input.task
    assert task.ref == "failing_action"
    assert task.action == "core.transform.reshape"
    assert task.args == {"value": "${{ 1/0 }}"}

    # Check that the failure event was set
    assert evt.failure is not None
    return evt


def assert_error_handler_initiated_correctly(
    events: list[WorkflowExecutionEvent],
    *,
    handler_dsl: DSLInput,
    handler_wf: Workflow,
    failing_wf_id: WorkflowID,
    failing_wf_exec_id: WorkflowExecutionID,
) -> WorkflowExecutionEvent[RunActionInput]:
    # # 5.1 Find the event where the error handler was called
    evt = next(
        (
            event
            for event in events
            if (
                event.event_type
                == WorkflowEventType.START_CHILD_WORKFLOW_EXECUTION_INITIATED
                and event.event_group
                and event.event_group.action_ref is None
                and event.event_group.action_title == "Testing Error Handler"
            )
        ),
        None,
    )
    assert evt is not None, "No error handler event found"
    assert evt.event_group is not None
    assert isinstance(evt.event_group, EventGroup)
    group = evt.event_group
    assert isinstance(group.action_input, DSLRunArgs)
    # Check that the error handler DSL was passed correctly
    assert group.action_input.dsl == handler_dsl

    # Check that the error handler's parent is the failing workflow
    assert (
        group.action_input.parent_run_context
        and group.action_input.parent_run_context.wf_id == failing_wf_id
    )
    assert (
        group.action_input.parent_run_context
        and group.action_input.parent_run_context.wf_exec_id == failing_wf_exec_id
    )

    # Check that the error handler received the correct error context
    assert group.action_input.trigger_inputs == {
        "errors": {
            "failing_action": {
                "attempt": 1,
                "expr_context": "ACTIONS",
                "message": "There was an error in the executor when calling action 'core.transform.reshape' (500).\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\nbinary_op\n  literal\t1\n  /\n  literal\t0\n\n```\nReason: Error trying to process rule \"binary_op\":\n\nCannot divide by zero\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 74",
                "ref": "failing_action",
                "type": "ExecutorClientError",
            }
        },
        "handler_wf_id": handler_wf.id,
        "message": "Workflow failed with 1 task exception(s)\n\n==================== (1/1) ACTIONS.failing_action ====================\n\nExecutorClientError: [ACTIONS.failing_action -> run_action] (Attempt 1)\n\nThere was an error in the executor when calling action 'core.transform.reshape' (500).\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\nbinary_op\n  literal\t1\n  /\n  literal\t0\n\n```\nReason: Error trying to process rule \"binary_op\":\n\nCannot divide by zero\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 74",
        "orig_wf_exec_id": failing_wf_exec_id,
        "orig_wf_id": failing_wf_id,
    }
    return evt


def assert_error_handler_started(
    events: list[WorkflowExecutionEvent],
) -> WorkflowExecutionEvent:
    evt = next(
        (
            event
            for event in events
            if (
                event.event_type == WorkflowEventType.CHILD_WORKFLOW_EXECUTION_STARTED
                and event.event_group
                and event.event_group.action_ref is None
                and event.event_group.action_title == "Testing Error Handler"
            )
        ),
        None,
    )
    assert evt is not None, "No error handler started event found"
    return evt


def assert_error_handler_completed(
    events: list[WorkflowExecutionEvent],
) -> WorkflowExecutionEvent:
    evt = next(
        (
            event
            for event in events
            if (
                event.event_type == WorkflowEventType.CHILD_WORKFLOW_EXECUTION_COMPLETED
                and event.event_group
                and event.event_group.action_ref is None
                and event.event_group.action_title == "Testing Error Handler"
            )
        ),
        None,
    )
    assert evt is not None, "No error handler completed event found"
    return evt


@pytest.mark.parametrize("mode", ["id", "alias"])
@pytest.mark.integration
@pytest.mark.anyio
async def test_workflow_error_handler_success(
    test_role: Role,
    temporal_client: Client,
    mode: Literal["id", "alias"],
    error_handler_wf_and_dsl: ErrorHandlerWfAndDslT,
    failing_dsl: DSLInput,
):
    """
    Test that the error handler can capture errors.
    Then, verify that the error handler was run.
    Run with both workflow id and alias.

    1. Create an error handler
    2. Create failing workflow (1/0)
    3. Run the failing workflow
    4.Check that the error handler is called
    5. Check that the error handler has the correct context
    """

    # 1. Create an error handler
    handler_dsl = error_handler_wf_and_dsl.dsl
    handler_wf = error_handler_wf_and_dsl.wf

    # 2. Create a failing workflow
    wf_exec_id = generate_test_exec_id(test_workflow_error_handler_success.__name__)

    match mode:
        case "id":
            error_handler = handler_wf.id
        case "alias":
            error_handler = handler_wf.alias
        case _:
            raise ValueError(f"Invalid mode: {mode}")
    failing_dsl.error_handler = error_handler

    # 3. Run the failing workflow
    run_args = DSLRunArgs(
        dsl=failing_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    with pytest.raises(WorkflowFailureError) as exc_info:
        _ = await _run_workflow(temporal_client, wf_exec_id, run_args)
    assert str(exc_info.value) == "Workflow execution failed"

    # Check temporal event history
    exec_svc = await WorkflowExecutionsService.connect(role=test_role)
    events = await exec_svc.list_workflow_execution_events(wf_exec_id)
    assert len(events) > 0

    # 4. Verify the failing task is in the event history
    fail_evt = assert_erroneous_task_failed_correctly(events)

    # 5. Verify the error handler was called
    eh_init_evt = assert_error_handler_initiated_correctly(
        events,
        handler_dsl=handler_dsl,
        handler_wf=handler_wf,
        failing_wf_id=TEST_WF_ID,
        failing_wf_exec_id=wf_exec_id,
    )

    # 6. Verify that the error handler started and completed
    eh_start_evt = assert_error_handler_started(events)
    eh_complete_evt = assert_error_handler_completed(events)

    # N. Verify that the error handler was called after the failing task
    logger.info(f"Failing event id: {fail_evt.event_id}")
    logger.info(f"Error handler init event id: {eh_init_evt.event_id}")
    logger.info(f"Error handler start event id: {eh_start_evt.event_id}")
    logger.info(f"Error handler complete event id: {eh_complete_evt.event_id}")
    assert (
        fail_evt.event_id
        < eh_init_evt.event_id
        < eh_start_evt.event_id
        < eh_complete_evt.event_id
    ), (
        f"Event order is not correct: {fail_evt.event_id} < {eh_init_evt.event_id} < {eh_start_evt.event_id} < {eh_complete_evt.event_id}"
    )


@pytest.mark.parametrize(
    "id_or_alias,expected_err_msg",
    [
        pytest.param(
            "wf-00000000000000000000000000000000",
            "TracecatException: Workflow definition not found for 'wf-00000000000000000000000000000000', version=None",
            id="id-no-match",
        ),
        pytest.param(
            "invalid_error_handler",
            "RuntimeError: Couldn't find matching workflow for alias 'invalid_error_handler'",
            id="alias-no-match",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.anyio
async def test_workflow_error_handler_invalid_handler_fail_no_match(
    test_role: Role,
    temporal_client: Client,
    failing_dsl: DSLInput,
    id_or_alias: str,
    expected_err_msg: str,
):
    """
    Test that the error handler fails with an invalid error handler that has no matching workflow

    1. Create an error handler
    2. Create a failing workflow
    3. Run the failing workflow
    4. Check that the error handler fails
    """
    test_name = test_workflow_error_handler_invalid_handler_fail_no_match.__name__

    # Set an invalid error handler
    failing_dsl.error_handler = id_or_alias

    # Run the failing workflow
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(
        dsl=failing_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    with pytest.raises(WorkflowFailureError) as exc_info:
        _ = await _run_workflow(temporal_client, wf_exec_id, run_args)
    assert str(exc_info.value) == "Workflow execution failed"
    cause0 = exc_info.value.cause
    assert isinstance(cause0, ActivityError)
    cause1 = cause0.cause
    assert isinstance(cause1, ApplicationError)
    assert str(cause1) == expected_err_msg
