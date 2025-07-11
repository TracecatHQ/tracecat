"""Unit tests for workflows.

Objectives
----------
1. Test that the workflows can be executed in an isolated environment
2. Test that the workflows can be executed with the correct inputs and outputs
3. Test that the workflows can be executed with the correct ordering

"""

import asyncio
import os
import re
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import pytest
import yaml
from pydantic import SecretStr
from temporalio.api.enums.v1.workflow_pb2 import ParentClosePolicy
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
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
from tracecat.dsl.common import (
    RETRY_POLICIES,
    DSLEntrypoint,
    DSLInput,
    DSLRunArgs,
)
from tracecat.dsl.enums import (
    JoinStrategy,
    LoopStrategy,
    StreamErrorHandlingStrategy,
    WaitStrategy,
)
from tracecat.dsl.models import (
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    GatherArgs,
    RunActionInput,
    ScatterArgs,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.expressions.common import ExprContext
from tracecat.identifiers.workflow import (
    WF_EXEC_ID_PATTERN,
    WorkflowExecutionID,
    WorkflowID,
    WorkflowUUID,
)
from tracecat.logger import logger
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.tables.enums import SqlType
from tracecat.tables.models import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService
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
async def test_workflow_can_run_from_yaml(
    dsl, test_role, temporal_client, test_worker_factory
):
    test_name = f"test_workflow_can_run_from_yaml-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    # Run workflow
    async with test_worker_factory(temporal_client):
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
async def test_workflow_ordering_is_correct(
    dsl, test_role, temporal_client, test_worker_factory
):
    """We need to test that the ordering of the workflow tasks is correct."""

    # Connect client
    test_name = f"test_workflow_ordering_is_correct-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Run a worker for the activities and workflow
    async with test_worker_factory(temporal_client):
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
    ],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.anyio
async def test_workflow_completes_and_correct(
    dsl_with_expected, test_role, runtime_config, test_worker_factory
):
    dsl, expected = dsl_with_expected
    test_name = f"test_correctness_execution-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with test_worker_factory(client):
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
async def test_stress_workflow(dsl, test_role, test_worker_factory):
    """Test that we can have multiple executions of the same workflow running at the same time."""
    test_name = f"test_stress_workflow-{dsl.title}"
    client = await get_temporal_client()

    tasks: list[asyncio.Task] = []
    async with test_worker_factory(client):
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
async def test_stress_workflow_correctness(
    runs, test_role, temporal_client, test_worker_factory
):
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

    async with test_worker_factory(temporal_client):
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
async def test_workflow_set_environment_correct(
    test_role, temporal_client, test_worker_factory
):
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
    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
    assert result == "__TEST_ENVIRONMENT__"


@pytest.mark.anyio
async def test_workflow_override_environment_correct(
    test_role, temporal_client, test_worker_factory
):
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
    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
    assert result == "__CORRECT_ENVIRONMENT__"


@pytest.mark.anyio
async def test_workflow_default_environment_correct(
    test_role, temporal_client, test_worker_factory
):
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
    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
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
        workflow_id = WorkflowUUID.new(workflow.id)
        if alias:
            await mgmt_service.update_workflow(workflow_id, WorkflowUpdate(alias=alias))
        constructed_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Commit the child workflow
        defn_service = WorkflowDefinitionsService(session, role=role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow_id, dsl=constructed_dsl
        )
        return workflow


async def _run_workflow(
    wf_exec_id: str,
    run_args: DSLRunArgs,
    worker: Worker,
):
    async with worker:
        result = await worker.client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=worker.task_queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
    return result


@pytest.mark.anyio
async def test_child_workflow_success(test_role, temporal_client, test_worker_factory):
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
    logger.info(
        "Running workflow",
        wf_exec_id=wf_exec_id,
        t=type(test_worker_factory),
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)

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
async def test_child_workflow_context_passing(
    test_role, temporal_client, test_worker_factory
):
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
    parent_workflow_id = WorkflowUUID.new("wf-00000000000000000000000000000002")
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

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_worker_factory: Callable[[Client], Worker],
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
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
        trigger_inputs={
            "data": "__EXPECTED_DATA__",
        },
    )

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    test_worker_factory: Callable[[Client], Worker],
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
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_worker_factory: Callable[[Client], Worker],
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
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
        trigger_inputs="__EXPECTED_DATA__",
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_role, temporal_client, test_worker_factory
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

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_role, temporal_client, test_worker_factory
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

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_role, temporal_client, test_worker_factory
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

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_role, temporal_client, test_worker_factory
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

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
    test_role, temporal_client, test_worker_factory
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

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
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
async def test_pull_based_workflow_fetches_latest_version(
    temporal_client, test_role, test_worker_factory
):
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
        workflow_id = WorkflowUUID.new(workflow.id)

        # 2) Create first workflow definition
        defn_service = WorkflowDefinitionsService(session, role=test_role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow_id, dsl=constructed_dsl
        )

    run_args = DSLRunArgs(
        role=test_role,
        wf_id=workflow_id,
        # NOTE: Not setting dsl here to make it pull based
        # Not setting schedule_id here to make it use the passed in trigger inputs
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(f"{wf_exec_id}:first", run_args, worker)

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
            workflow_id=workflow_id, dsl=second_dsl
        )

    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(f"{wf_exec_id}:second", run_args, worker)
    assert result == "__EXPECTED_SECOND_RESULT__"


# Get the line number dynamically
PARTIAL_DIVISION_BY_ZERO_ERROR = {
    "ref": "start",
    "message": (
        "There was an error in the executor when calling action 'core.transform.reshape'.\n"
        "\n"
        "\n"
        "TracecatExpressionError: Error evaluating expression `1/0`\n"
        "\n"
        "[evaluator] Evaluation failed at node:\n"
        "```\n"
        "div_op\n"
        "  literal\t1\n"
        "  literal\t0\n"
        "\n"
        "```\n"
        'Reason: Error trying to process rule "div_op":\n'
        "\n"
        "Cannot divide by zero\n"
        "\n"
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
async def test_workflow_error_path(
    test_role, runtime_config, dsl_data, expected, test_worker_factory
):
    dsl = DSLInput(**dsl_data)
    test_name = f"test_workflow_error-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with test_worker_factory(client):
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
async def test_workflow_join_unreachable(
    test_role, runtime_config, test_worker_factory
):
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

    async with test_worker_factory(client):
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
async def test_workflow_multiple_entrypoints(
    test_role, runtime_config, test_worker_factory
):
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

    async with test_worker_factory(client):
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
    test_worker_factory,
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

    async with test_worker_factory(temporal_client):
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
        workflow_id = WorkflowUUID.new(workflow.id)
        if alias:
            await mgmt_service.update_workflow(workflow_id, WorkflowUpdate(alias=alias))
        constructed_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Commit the child workflow
        defn_service = WorkflowDefinitionsService(session, role=test_role)
        await defn_service.create_workflow_definition(
            workflow_id=workflow_id, dsl=constructed_dsl
        )
        try:
            yield ErrorHandlerWfAndDslT(dsl, workflow)
        finally:
            await mgmt_service.delete_workflow(workflow_id)


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
    workspace_id: str,
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
    match = re.match(WF_EXEC_ID_PATTERN, failing_wf_exec_id)
    if not match:
        pytest.fail(f"Invalid workflow execution ID: {failing_wf_exec_id}")
    wf_id_short = WorkflowUUID.new(failing_wf_id).short()
    exec_id = match.group("execution_id")
    wf_exec_url = f"http://localhost/workspaces/{workspace_id}/workflows/{wf_id_short}/executions/{exec_id}"

    assert group.action_input.trigger_inputs == {
        "errors": [
            {
                "attempt": 1,
                "expr_context": "ACTIONS",
                "message": (
                    "There was an error in the executor when calling action 'core.transform.reshape'.\n\n"
                    "\n"
                    "TracecatExpressionError: Error evaluating expression `1/0`\n\n"
                    "[evaluator] Evaluation failed at node:\n"
                    "```\n"
                    "div_op\n"
                    "  literal\t1\n"
                    "  literal\t0\n\n"
                    "```\n"
                    'Reason: Error trying to process rule "div_op":\n\n'
                    "Cannot divide by zero\n\n"
                    "\n"
                    "------------------------------\n"
                    "File: /app/tracecat/expressions/core.py\n"
                    "Function: result\n"
                    "Line: 73"
                ),
                "ref": "failing_action",
                "type": "ExecutorClientError",
            }
        ],
        "handler_wf_id": str(WorkflowUUID.new(handler_wf.id)),
        "message": (
            "Workflow failed with 1 task exception(s)\n\n"
            "==================== (1/1) ACTIONS.failing_action ====================\n\n"
            "ExecutorClientError: [ACTIONS.failing_action -> run_action] (Attempt 1)\n\n"
            "There was an error in the executor when calling action 'core.transform.reshape'.\n\n"
            "\n"
            "TracecatExpressionError: Error evaluating expression `1/0`\n\n"
            "[evaluator] Evaluation failed at node:\n"
            "```\n"
            "div_op\n"
            "  literal\t1\n"
            "  literal\t0\n\n"
            "```\n"
            'Reason: Error trying to process rule "div_op":\n\n'
            "Cannot divide by zero\n\n"
            "\n"
            "------------------------------\n"
            "File: /app/tracecat/expressions/core.py\n"
            "Function: result\n"
            "Line: 73"
        ),
        "orig_wf_exec_id": failing_wf_exec_id,
        "orig_wf_exec_url": wf_exec_url,
        "orig_wf_title": "Division by zero",
        "trigger_type": "manual",
        "orig_wf_id": str(failing_wf_id),
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
    test_worker_factory,
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

    workspace_id = test_role.workspace_id
    if workspace_id is None:
        raise ValueError("Workspace ID is not set")

    # 1. Create an error handler
    handler_dsl = error_handler_wf_and_dsl.dsl
    handler_wf = error_handler_wf_and_dsl.wf

    # 2. Create a failing workflow
    wf_exec_id = generate_test_exec_id(test_workflow_error_handler_success.__name__)

    match mode:
        case "id":
            error_handler = WorkflowUUID.new(handler_wf.id).short()
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
        worker = test_worker_factory(temporal_client)
        _ = await _run_workflow(wf_exec_id, run_args, worker)
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
        workspace_id=str(workspace_id),
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
            "TracecatException: Workflow definition not found for WorkflowUUID('00000000-0000-0000-0000-000000000000'), version=None",
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
    test_worker_factory,
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
        worker = test_worker_factory(temporal_client)
        _ = await _run_workflow(wf_exec_id, run_args, worker)
    assert str(exc_info.value) == "Workflow execution failed"
    cause0 = exc_info.value.cause
    assert isinstance(cause0, ActivityError)
    cause1 = cause0.cause
    assert isinstance(cause1, ApplicationError)
    assert str(cause1) == expected_err_msg


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_lookup_table_success(
    test_role: Role, temporal_client: Client, test_admin_role: Role, test_worker_factory
):
    """
    Test that a workflow can lookup a table

    1. Create a table
    2. Create a workflow that looks up the table
    3. Run the workflow
    4. Check that the workflow can lookup the table
    """
    test_name = test_workflow_lookup_table_success.__name__

    async with TablesService.with_session(role=test_admin_role) as service:
        table = await service.create_table(TableCreate(name="test"))
        await service.create_column(
            table,
            TableColumnCreate(name="number", type=SqlType.INTEGER),
        )
        await service.insert_row(
            table,
            TableRowInsert(data={"number": 1}),
        )

    dsl = DSLInput(
        title=test_name,
        description="Test Workflow",
        entrypoint=DSLEntrypoint(ref="lookup_table"),
        actions=[
            ActionStatement(
                ref="lookup_table",
                action="core.table.lookup",
                args={
                    "table": table.name,
                    "column": "number",
                    "value": 1,
                },
            ),
        ],
        returns="${{ ACTIONS.lookup_table.result }}",
    )
    # Run the workflow
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
    assert "number" in result
    assert result["number"] == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_lookup_table_missing_value(
    test_role: Role, temporal_client: Client, test_admin_role: Role, test_worker_factory
):
    """
    Test that a workflow returns None when looking up a non-existent value in a table.

    1. Create a table with a single row
    2. Create a workflow that looks up a non-existent value
    3. Run the workflow
    4. Check that the workflow returns None
    """
    test_name = test_workflow_lookup_table_missing_value.__name__

    # Create table with a single row containing number=1
    async with TablesService.with_session(role=test_admin_role) as service:
        table = await service.create_table(TableCreate(name="test_missing"))
        await service.create_column(
            table,
            TableColumnCreate(name="number", type=SqlType.INTEGER),
        )
        await service.insert_row(
            table,
            TableRowInsert(data={"number": 1}),
        )

    # Create workflow that looks up non-existent value (2)
    dsl = DSLInput(
        title=test_name,
        description="Test Workflow",
        entrypoint=DSLEntrypoint(ref="lookup_table"),
        actions=[
            ActionStatement(
                ref="lookup_table",
                action="core.table.lookup",
                args={
                    "table": table.name,
                    "column": "number",
                    "value": 2,  # This value doesn't exist in the table
                },
            ),
        ],
        returns="${{ ACTIONS.lookup_table.result }}",
    )

    # Run the workflow
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)
    assert result is None


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_insert_table_row_success(
    test_role: Role, temporal_client: Client, test_admin_role: Role, test_worker_factory
):
    """
    Test that a workflow can insert a row into a table.

    1. Create a table with a column
    2. Create a workflow that inserts a row into the table
    3. Run the workflow
    4. Verify the row was inserted correctly
    """
    test_name = test_workflow_insert_table_row_success.__name__

    # Create table with a number column
    table_name = None
    async with TablesService.with_session(role=test_admin_role) as service:
        table = await service.create_table(TableCreate(name="test_insert"))
        table_name = table.name
        await service.create_column(
            table,
            TableColumnCreate(name="number", type=SqlType.INTEGER),
        )

    # Create workflow that inserts a row
    dsl = DSLInput(
        title=test_name,
        description="Test inserting a row into a table via workflow",
        entrypoint=DSLEntrypoint(ref="insert_row"),
        actions=[
            ActionStatement(
                ref="insert_row",
                action="core.table.insert_row",
                args={
                    "table": table.name,
                    "row_data": {"number": 42},
                },
            ),
        ],
        returns="${{ ACTIONS.insert_row.result }}",
    )

    # Run the workflow
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)

    # Verify the result indicates success
    assert result is not None
    assert isinstance(result, Mapping)
    assert "id" in result
    assert "number" in result
    assert result["number"] == 42

    # Verify the row was actually inserted
    async with TablesService.with_session(role=test_admin_role) as service:
        rows = await service.lookup_rows(table_name, columns=["number"], values=[42])
    assert len(rows) == 1
    assert rows[0]["number"] == 42


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_table_actions_in_loop(
    test_role: Role, temporal_client: Client, test_admin_role: Role, test_worker_factory
):
    """
    Test that a workflow can perform table operations in a loop.

    1. Create a table with a column
    2. Create a workflow that inserts multiple rows in a loop
    3. Run the workflow
    4. Verify all rows were inserted correctly
    5. Verify lookup works for each inserted row
    """
    test_name = test_workflow_table_actions_in_loop.__name__

    # Create table with a number column
    table_name = None
    async with TablesService.with_session(role=test_admin_role) as service:
        table = await service.create_table(TableCreate(name="test_loop_table"))
        table_name = table.name
        await service.create_column(
            table,
            TableColumnCreate(name="number", type=SqlType.INTEGER),
        )
        await service.create_column(
            table,
            TableColumnCreate(name="squared", type=SqlType.INTEGER),
        )

    # Create workflow that inserts rows in a loop and then looks them up
    dsl = DSLInput(
        title=test_name,
        description="Test running table actions in a loop",
        entrypoint=DSLEntrypoint(ref="process_numbers"),
        actions=[
            # Insert rows in a loop
            ActionStatement(
                ref="insert_rows",
                action="core.table.insert_row",
                for_each="${{ for var.num in FN.range(1, 6) }}",  # Loop from 1 to 5
                args={
                    "table": table_name,
                    "row_data": {
                        "number": "${{ var.num }}",
                        "squared": "${{ var.num * var.num }}",
                    },
                },
            ),
            # Look up rows in a loop
            ActionStatement(
                ref="lookup_rows",
                action="core.table.lookup",
                for_each="${{ for var.num in FN.range(1, 6) }}",  # Loop from 1 to 5
                args={
                    "table": table_name,
                    "column": "number",
                    "value": "${{ var.num }}",
                },
                depends_on=["insert_rows"],
            ),
            # Process the results
            ActionStatement(
                ref="process_numbers",
                action="core.transform.reshape",
                args={
                    "value": {
                        "inserted_rows": "${{ ACTIONS.insert_rows.result }}",
                        "looked_up_rows": "${{ ACTIONS.lookup_rows.result }}",
                    }
                },
                depends_on=["lookup_rows"],
            ),
        ],
        returns="${{ ACTIONS.process_numbers.result }}",
    )

    # Run the workflow
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    worker = test_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker)

    # Verify the results
    assert result is not None
    assert "inserted_rows" in result
    assert "looked_up_rows" in result

    # Check inserted rows
    inserted_rows = result["inserted_rows"]
    assert len(inserted_rows) == 5
    for i, row in enumerate(inserted_rows, 1):
        assert row["number"] == i
        assert row["squared"] == i * i

    # Check looked up rows
    looked_up_rows = result["looked_up_rows"]
    assert len(looked_up_rows) == 5
    for i, row in enumerate(looked_up_rows, 1):
        assert row["number"] == i
        assert row["squared"] == i * i

    # Verify the rows were actually inserted in the database
    async with TablesService.with_session(role=test_admin_role) as service:
        table = await service.get_table_by_name(table_name)
        rows = await service.list_rows(table)

    assert len(rows) == 5
    for i, row in enumerate(sorted(rows, key=lambda r: r["number"]), 1):
        assert row["number"] == i
        assert row["squared"] == i * i


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_detached_child_workflow(
    test_role: Role, temporal_client: Client, test_worker_factory
):
    """
    Test that a workflow can detach a child workflow.
    Logic:
    1. Parent workflow creates child workflow
    2. Terminate parent workflow
    3. Child workflow runs to completion
    4. Verify child workflow completed successfully
    """
    test_name = f"{test_workflow_detached_child_workflow.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)
    # Child
    child_dsl = DSLInput(
        title="Test child workflow detached",
        description="Test child workflow detached",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                args={
                    "value": "${{ TRIGGER + 1000 }}",
                },
                start_delay=2,
            ),
        ],
        inputs={},
        returns="${{ ACTIONS.a.result }}",
    )

    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)
    # Parent
    parent_dsl = DSLInput(
        title="Parent",
        description="Test parent workflow can call child correctly",
        entrypoint=DSLEntrypoint(ref="parent"),
        actions=[
            ActionStatement(
                ref="parent",
                action="core.workflow.execute",
                for_each="${{ for var.x in [1,2,3] }}",
                args={
                    "workflow_id": child_workflow.id,
                    "trigger_inputs": "${{ var.x }}",
                    "wait_strategy": WaitStrategy.DETACH.value,
                    "timeout": "123",
                },
            ),
        ],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )
    worker = test_worker_factory(temporal_client)
    async with worker:
        parent_handle = await temporal_client.start_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=worker.task_queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        # Wait for parent completion
        await parent_handle.result()
        desc = await parent_handle.describe()
        pending_children = desc.raw_description.pending_children
        assert len(pending_children) == 3
        async with GatheringTaskGroup() as tg:
            for child in sorted(pending_children, key=lambda c: c.initiated_id):
                logger.info("child", child=child)
                child_handle = temporal_client.get_workflow_handle_for(
                    DSLWorkflow.run, child.workflow_id
                )
                child_desc = await child_handle.describe()
                assert (
                    child.parent_close_policy
                    == ParentClosePolicy.PARENT_CLOSE_POLICY_ABANDON
                ), (
                    f"Child {child.workflow_id} has parent close policy {child.parent_close_policy}"
                )
                assert child_desc.status == WorkflowExecutionStatus.RUNNING, (
                    f"Child {child.workflow_id} is not running"
                )

                tg.create_task(child_handle.result())

        results = tg.results()
        assert results == [1001, 1002, 1003]


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(
    "dsl,expected",
    [
        # 1. Single scatter-reshape-gather (original)
        pytest.param(
            DSLInput(
                title="Single scatter-gather",
                description="Test single scatter-gather",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    # This doesn't output any result
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [1,2, 3] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ FN.add(ACTIONS.scatter.result, 1) }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["reshape"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape.result }}",
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {"gather": {"result": [2, 3, 4], "result_typename": "list"}},
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="basic-for-loop",
        ),
        pytest.param(
            DSLInput(
                title="Single scatter-gather with surrounding actions",
                description="Test single scatter-gather with surrounding actions",
                entrypoint=DSLEntrypoint(ref="a"),
                actions=[
                    # This doesn't output any result
                    ActionStatement(
                        ref="a",
                        action="core.transform.reshape",
                        args={"value": [1, 2, 3]},
                    ),
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        depends_on=["a"],
                        args=ScatterArgs(
                            collection="${{ ACTIONS.a.result }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="b",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ FN.add(ACTIONS.scatter.result, 1) }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["b"],
                        args=GatherArgs(items="${{ ACTIONS.b.result }}").model_dump(),
                    ),
                    ActionStatement(
                        ref="c",
                        action="core.transform.reshape",
                        depends_on=["gather"],
                        args={"value": "${{ ACTIONS.gather.result }}"},
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "a": {"result": [1, 2, 3], "result_typename": "list"},
                    "gather": {"result": [2, 3, 4], "result_typename": "list"},
                    "c": {"result": [2, 3, 4], "result_typename": "list"},
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-gather-with-surrounding-actions",
        ),
        # 2. Nested scatter-gather (original)
        pytest.param(
            DSLInput(
                title="Nested scatter-gather",
                description="Test nested scatter-gather",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    # This doesn't output any result
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [[1,2], [3,4]] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="scatter2",
                        action="core.transform.scatter",
                        depends_on=["scatter"],
                        args=ScatterArgs(
                            collection="${{ ACTIONS.scatter.result }}",
                        ).model_dump(),
                    ),
                    # Go parallel
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        args={"value": "${{ FN.add(ACTIONS.scatter2.result, 1) }}"},
                    ),
                    ActionStatement(
                        ref="reshape2",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        args={"value": "${{ FN.add(ACTIONS.scatter2.result, 2) }}"},
                    ),
                    # How do we now handle the parallel execution streams?
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["reshape", "reshape2"],
                        args=GatherArgs(
                            # When an execution stream hits an gather matching
                            # the current
                            # This will grab the result of the reshape action
                            # in its execution scope
                            items="${{ ACTIONS.reshape.result }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="gather2",
                        action="core.transform.gather",
                        depends_on=["gather"],
                        args=GatherArgs(
                            items="${{ ACTIONS.gather.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather2": {"result": [[2, 3], [4, 5]], "result_typename": "list"}
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="nested-for-loop",
        ),
        # 3. Scatter-Gather followed by Scatter-Gather (original)
        pytest.param(
            DSLInput(
                title="Scatter-Gather followed by Scatter-Gather",
                description="Test two sequential scatter-gather blocks",
                entrypoint=DSLEntrypoint(ref="scatter1"),
                actions=[
                    # First scatter-gather block
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [10, 20] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape1",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        args={"value": "${{ FN.add(ACTIONS.scatter1.result, 1) }}"},
                    ),
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["reshape1"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape1.result }}"
                        ).model_dump(),
                    ),
                    # Second scatter-gather block, using result of first
                    ActionStatement(
                        ref="scatter2",
                        action="core.transform.scatter",
                        depends_on=["gather1"],
                        args=ScatterArgs(
                            collection="${{ ACTIONS.gather1.result }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape2",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        args={"value": "${{ FN.add(ACTIONS.scatter2.result, 100) }}"},
                    ),
                    ActionStatement(
                        ref="gather2",
                        action="core.transform.gather",
                        depends_on=["reshape2"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape2.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    # First block: [10, 20] -> [11, 21]
                    # Second block: [11, 21] -> [111, 121]
                    "gather1": {"result": [11, 21], "result_typename": "list"},
                    "gather2": {"result": [111, 121], "result_typename": "list"},
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="sequential-scatter-gather",
        ),
        # Parallel scatter/gather blocks, then join
        pytest.param(
            DSLInput(
                title="Parallel Scatter-Gather blocks joined",
                description=(
                    "Test two scatter/reshape/gather blocks running in parallel, "
                    "then joined in a final action. The structure is: "
                ),
                entrypoint=DSLEntrypoint(ref="start"),
                actions=[
                    # Start splits into two parallel scatters
                    ActionStatement(
                        ref="ex1",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [1, 2] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="ex2",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [10, 20] }}",
                        ).model_dump(),
                    ),
                    # Reshape in each branch
                    ActionStatement(
                        ref="a",
                        action="core.transform.reshape",
                        depends_on=["ex1"],
                        args={"value": "${{ FN.mul(ACTIONS.ex1.result, 2) }}"},
                    ),
                    ActionStatement(
                        ref="b",
                        action="core.transform.reshape",
                        depends_on=["ex2"],
                        args={"value": "${{ FN.add(ACTIONS.ex2.result, 5) }}"},
                    ),
                    # Gather in each branch
                    ActionStatement(
                        ref="im1",
                        action="core.transform.gather",
                        depends_on=["a"],
                        args=GatherArgs(items="${{ ACTIONS.a.result }}").model_dump(),
                    ),
                    ActionStatement(
                        ref="im2",
                        action="core.transform.gather",
                        depends_on=["b"],
                        args=GatherArgs(items="${{ ACTIONS.b.result }}").model_dump(),
                    ),
                    # Join both results in C
                    ActionStatement(
                        ref="c",
                        action="core.transform.reshape",
                        depends_on=["im1", "im2"],
                        args={
                            "value": "${{ ACTIONS.im1.result + ACTIONS.im2.result }}"
                        },
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    # ex1: [1,2] -> A: [2,4] -> im1: [2,4]
                    # ex2: [10,20] -> B: [15,25] -> im2: [15,25]
                    # C: [2,4,15,25]
                    "im1": {"result": [2, 4], "result_typename": "list"},
                    "im2": {"result": [15, 25], "result_typename": "list"},
                    "c": {"result": [2, 4, 15, 25], "result_typename": "list"},
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="parallel-scatter-gather-join",
        ),
        # 4. Scatter followed by gather directly (no action in between)
        pytest.param(
            DSLInput(
                title="Scatter then Gather (no intermediate action)",
                description="Test scatter followed by gather directly",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [5, 6, 7] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["scatter"],
                        args=GatherArgs(
                            items="${{ ACTIONS.scatter.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {"gather": {"result": [5, 6, 7], "result_typename": "list"}},
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-gather-direct",
        ),
        # 5. Scatter -> reshape (run_if even) -> gather
        pytest.param(
            DSLInput(
                title="Scatter-reshape (even only) then gather",
                description="Test scatter, reshape only if even, then gather",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [1, 2, 3, 4, 5, 6] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        # Only run reshape if the value is even
                        run_if="${{ FN.mod(ACTIONS.scatter.result, 2) == 0 }}",
                        args={"value": "${{ FN.mul(ACTIONS.scatter.result, 10) }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["reshape"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    # Only even numbers: 2, 4, 6 -> 20, 40, 60
                    # Unset values are automatically removed
                    "gather": {
                        "result": [20, 40, 60],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-reshape-even-gather",
        ),
        # 6. Scatter -> reshape (run_if even) -> gather with drop_nulls
        pytest.param(
            DSLInput(
                title="Scatter-reshape (even only) then gather with drop_nulls",
                description="Test scatter, reshape only if even, then gather with drop_nulls",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection="${{ [1, 2, 3, 4, 5, 6] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        # Only run reshape if the value is even
                        run_if="${{ FN.mod(ACTIONS.scatter.result, 2) == 0 }}",
                        args={"value": "${{ FN.mul(ACTIONS.scatter.result, 10) }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["reshape"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape.result }}",
                            drop_nulls=True,
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    # Only even numbers: 2, 4, 6 -> 20, 40, 60
                    # Everything else is dropped
                    "gather": {
                        "result": [20, 40, 60],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-reshape-even-gather-drop-nulls",
        ),
        pytest.param(
            DSLInput(
                title="Scatter-reshape (even only) with Nones, then gather with drop_nulls",
                description="Test scatter with Nones, reshape only if even, then gather with drop_nulls only",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            # Data with Nones in the collection
                            collection="${{ [1, None, None, 2, 3, None, 4, 5, 6, None] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["reshape"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape.result }}",
                            drop_nulls=True,
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    # Skip all nulls
                    "gather": {
                        "result": [1, 2, 3, 4, 5, 6],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-reshape-even-gather-drop-nulls-with-nones",
        ),
        # New test: A -> scatter -> B -> gather -> C, all reshapes, skip scatter, expect only A to run
        pytest.param(
            DSLInput(
                title="Skip scatter directly, only first action (a) runs",
                description="Test that if scatter is skipped, only a runs and downstream tasks are not executed.",
                entrypoint=DSLEntrypoint(ref="a"),
                actions=[
                    ActionStatement(
                        ref="a",
                        action="core.transform.reshape",
                        args={"value": "${{ 42 }}"},
                    ),
                    # SKIPPED
                    # ======== Block 1 ========
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        depends_on=["a"],
                        # Always skip scatter
                        run_if="${{ False }}",
                        args=ScatterArgs(
                            collection="${{ [1, 2, 3] }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="b",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["b"],
                        args=GatherArgs(
                            items="${{ ACTIONS.b.result }}",
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="c",
                        action="core.transform.reshape",
                        depends_on=["gather"],
                        args={"value": "${{ ACTIONS.gather.result }}"},
                    ),
                    # ======== End Block 1 ========
                    # Block 1 s houldn't run
                ],
            ),
            {
                "ACTIONS": {
                    "a": {
                        "result": 42,
                        "result_typename": "int",
                    }
                    # No other actions should have run
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="skip-scatter-directly-only-first-runs",
        ),
        # --- NEW TEST CASE: scatter -> a (run_if False) -> gather, expect empty array ---
        pytest.param(
            DSLInput(
                title="scatter->a(run_if False)->gather, all skipped, expect empty array",
                description=(
                    "Test that if all iterations of an action after scatter are skipped (run_if False), "
                    "the gather action receives only unset values and thus returns an empty array."
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
                    ),
                    ActionStatement(
                        ref="a",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        run_if="${{ False }}",
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["a"],
                        args=GatherArgs(items="${{ ACTIONS.a.result }}").model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather": {
                        "result": [],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="skip-all-in-scatter",
        ),
        # Mixed Collection Scenarios
        # 1. Parallel branches with different collection sizes
        pytest.param(
            DSLInput(
                title="Parallel branches with different collection sizes",
                description="Test parallel scatter-gather with collections of different lengths",
                entrypoint=DSLEntrypoint(ref="start"),
                actions=[
                    # Branch A: 3 items
                    ActionStatement(
                        ref="scatter_a",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
                    ),
                    ActionStatement(
                        ref="process_a",
                        action="core.transform.reshape",
                        depends_on=["scatter_a"],
                        args={"value": "${{ ACTIONS.scatter_a.result * 10 }}"},
                    ),
                    ActionStatement(
                        ref="gather_a",
                        action="core.transform.gather",
                        depends_on=["process_a"],
                        args=GatherArgs(
                            items="${{ ACTIONS.process_a.result }}"
                        ).model_dump(),
                    ),
                    # Branch B: 2 items
                    ActionStatement(
                        ref="scatter_b",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[100, 200]).model_dump(),
                    ),
                    ActionStatement(
                        ref="process_b",
                        action="core.transform.reshape",
                        depends_on=["scatter_b"],
                        args={"value": "${{ ACTIONS.scatter_b.result + 5 }}"},
                    ),
                    ActionStatement(
                        ref="gather_b",
                        action="core.transform.gather",
                        depends_on=["process_b"],
                        args=GatherArgs(
                            items="${{ ACTIONS.process_b.result }}"
                        ).model_dump(),
                    ),
                    # Join both branches
                    ActionStatement(
                        ref="join",
                        action="core.transform.reshape",
                        depends_on=["gather_a", "gather_b"],
                        args={
                            "value": "${{ ACTIONS.gather_a.result + ACTIONS.gather_b.result }}"
                        },
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather_a": {"result": [10, 20, 30], "result_typename": "list"},
                    "gather_b": {"result": [105, 205], "result_typename": "list"},
                    "join": {
                        "result": [10, 20, 30, 105, 205],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="parallel-different-sizes",
        ),
        # 2. Mixed data types preservation
        pytest.param(
            DSLInput(
                title="Mixed data types preservation",
                description="Test scatter-gather with different data types",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection=[1, "hello", {"key": "value"}, None, [1, 2]]
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="identity",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["identity"],
                        args=GatherArgs(
                            items="${{ ACTIONS.identity.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather": {
                        "result": [1, "hello", {"key": "value"}, None, [1, 2]],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="mixed-data-types",
        ),
        # 3. Variable nesting depths
        pytest.param(
            DSLInput(
                title="Variable nesting depths",
                description="Test scatter-gather with collections of inconsistent nesting",
                entrypoint=DSLEntrypoint(ref="outer_scatter"),
                actions=[
                    # Outer scatter with mixed nesting
                    ActionStatement(
                        ref="outer_scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection=[
                                [1, 2],  # 2D array
                                [[3, 4], [5, 6]],  # 3D array
                                7,  # scalar
                                [],  # empty array
                            ]
                        ).model_dump(),
                    ),
                    # Process each item based on its type
                    ActionStatement(
                        ref="process_item",
                        action="core.transform.reshape",
                        depends_on=["outer_scatter"],
                        args={"value": "${{ ACTIONS.outer_scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="outer_gather",
                        action="core.transform.gather",
                        depends_on=["process_item"],
                        args=GatherArgs(
                            items="${{ ACTIONS.process_item.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "outer_gather": {
                        "result": [[1, 2], [[3, 4], [5, 6]], 7, []],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="variable-nesting-depths",
        ),
        # 4. Empty and non-empty collections in parallel
        pytest.param(
            DSLInput(
                title="Empty and non-empty parallel branches",
                description="Test parallel scatter-gather where one branch has empty collection",
                entrypoint=DSLEntrypoint(ref="start"),
                actions=[
                    # Branch A: Non-empty collection
                    ActionStatement(
                        ref="scatter_nonempty",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
                    ),
                    ActionStatement(
                        ref="process_nonempty",
                        action="core.transform.reshape",
                        depends_on=["scatter_nonempty"],
                        args={"value": "${{ ACTIONS.scatter_nonempty.result * 2 }}"},
                    ),
                    ActionStatement(
                        ref="gather_nonempty",
                        action="core.transform.gather",
                        depends_on=["process_nonempty"],
                        args=GatherArgs(
                            items="${{ ACTIONS.process_nonempty.result }}"
                        ).model_dump(),
                    ),
                    # Branch B: Empty collection
                    ActionStatement(
                        ref="scatter_empty",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[]).model_dump(),
                    ),
                    ActionStatement(
                        ref="process_empty",
                        action="core.transform.reshape",
                        depends_on=["scatter_empty"],
                        args={"value": "${{ ACTIONS.scatter_empty.result + 100 }}"},
                    ),
                    ActionStatement(
                        ref="gather_empty",
                        action="core.transform.gather",
                        depends_on=["process_empty"],
                        args=GatherArgs(
                            items="${{ ACTIONS.process_empty.result }}"
                        ).model_dump(),
                    ),
                    # Final merge - only non-empty branch should contribute
                    ActionStatement(
                        ref="final_merge",
                        action="core.transform.reshape",
                        depends_on=["gather_nonempty", "gather_empty"],
                        args={
                            "value": "${{ ACTIONS.gather_nonempty.result + ACTIONS.gather_empty.result }}"
                        },
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather_nonempty": {"result": [2, 4, 6], "result_typename": "list"},
                    "gather_empty": {"result": [], "result_typename": "list"},
                    "final_merge": {"result": [2, 4, 6], "result_typename": "list"},
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="empty-nonempty-parallel",
        ),
        # 5. Collections with different lengths in nested scatter
        # This test case demonstrates a nested scatter-gather pattern where the outer scatter
        # iterates over a list of lists, each with a different length. The workflow is as follows:
        # 1. "outer_scatter" splits the input into three parallel branches: [1], [2, 3], and [4, 5, 6].
        # 2. For each branch, "inner_scatter" further scatters the inner list, so each number is processed independently.
        # 3. "double_value" doubles each number in the inner scatter.
        # 4. "inner_gather" collects the doubled values for each outer branch, resulting in:
        #    - [2] for [1]
        #    - [4, 6] for [2, 3]
        #    - [8, 10, 12] for [4, 5, 6]
        # 5. "outer_gather" collects the results from all outer branches, producing a list of lists.
        # The expected result is a nested list where each sublist contains the doubled values of the original inner list.
        pytest.param(
            DSLInput(
                title="Nested scatter with varying inner collection sizes",
                description="Test nested scatter where inner collections have different sizes",
                entrypoint=DSLEntrypoint(ref="outer_scatter"),
                actions=[
                    ActionStatement(
                        ref="outer_scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection=[
                                [1],  # 1 item
                                [2, 3],  # 2 items
                                [4, 5, 6],  # 3 items
                            ]
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="inner_scatter",
                        action="core.transform.scatter",
                        depends_on=["outer_scatter"],
                        args=ScatterArgs(
                            collection="${{ ACTIONS.outer_scatter.result }}"
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="double_value",
                        action="core.transform.reshape",
                        depends_on=["inner_scatter"],
                        args={"value": "${{ ACTIONS.inner_scatter.result * 2 }}"},
                    ),
                    ActionStatement(
                        ref="inner_gather",
                        action="core.transform.gather",
                        depends_on=["double_value"],
                        args=GatherArgs(
                            items="${{ ACTIONS.double_value.result }}"
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="outer_gather",
                        action="core.transform.gather",
                        depends_on=["inner_gather"],
                        args=GatherArgs(
                            items="${{ ACTIONS.inner_gather.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "outer_gather": {
                        # Explanation:
                        # - The first sublist [1] is doubled to [2]
                        # - The second sublist [2, 3] is doubled to [4, 6]
                        # - The third sublist [4, 5, 6] is doubled to [8, 10, 12]
                        # The gather operation preserves the nested structure.
                        "result": [[2], [4, 6], [8, 10, 12]],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="nested-varying-sizes",
        ),
        # 6. Mixed empty and non-empty in nested structure
        pytest.param(
            DSLInput(
                title="Mixed empty/non-empty in nested scatter",
                description="Test nested scatter with mix of empty and non-empty inner collections",
                entrypoint=DSLEntrypoint(ref="outer_scatter"),
                actions=[
                    ActionStatement(
                        ref="outer_scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection=[
                                [1, 2],  # Non-empty
                                [],  # Empty
                                [3],  # Non-empty
                            ]
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="inner_scatter",
                        action="core.transform.scatter",
                        depends_on=["outer_scatter"],
                        args=ScatterArgs(
                            collection="${{ ACTIONS.outer_scatter.result }}"
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="process_inner",
                        action="core.transform.reshape",
                        depends_on=["inner_scatter"],
                        args={"value": "${{ ACTIONS.inner_scatter.result + 10 }}"},
                    ),
                    ActionStatement(
                        ref="inner_gather",
                        action="core.transform.gather",
                        depends_on=["process_inner"],
                        args=GatherArgs(
                            items="${{ ACTIONS.process_inner.result }}"
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="outer_gather",
                        action="core.transform.gather",
                        depends_on=["inner_gather"],
                        args=GatherArgs(
                            items="${{ ACTIONS.inner_gather.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "outer_gather": {
                        "result": [[11, 12], [], [13]],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="nested-mixed-empty-nonempty",
        ),
        # Empty collection scatter
        pytest.param(
            DSLInput(
                title="Scatter empty collection",
                # Improved explanation:
                # This test verifies the correct behavior when the 'scatter' action receives an empty collection.
                # The expected behavior is:
                # - The 'scatter' action should not produce any execution streams, as there are no items to process.
                # - The 'gather' action, which depends on 'scatter', should be executed in the global context.
                # - The result of 'gather' should be an empty list, since there were no items to aggregate.
                # - No other actions should run, and the workflow should complete successfully.
                description=(
                    "Test that when an empty collection is provided to the 'scatter' action, "
                    "no execution streams are created and the dependent 'gather' action "
                    "returns an empty list as its result. This ensures that the workflow "
                    "handles empty collections gracefully and does not fail or produce "
                    "unexpected results."
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[]).model_dump(),
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["scatter"],
                        args=GatherArgs(
                            items="${{ ACTIONS.scatter.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather": {
                        "result": [],
                        "result_typename": "list",
                    }
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-empty-collection",
        ),
        pytest.param(
            DSLInput(
                title="Scatter empty collection with actions between",
                description="Test that an empty collection is scatterd and then gatherd with actions between",
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[]).model_dump(),
                    ),
                    # This should skip
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    # This should return an empty list
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["reshape"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape.result }}"
                        ).model_dump(),
                    ),
                    # Continue with a reshape
                    ActionStatement(
                        ref="reshape2",
                        action="core.transform.reshape",
                        depends_on=["gather"],
                        args={"value": "${{ ACTIONS.gather.result }}"},
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather": {"result": [], "result_typename": "list"},
                    "reshape2": {"result": [], "result_typename": "list"},
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scatter-empty-collection-between",
        ),
        pytest.param(
            DSLInput(
                title="Nested scatter with empty collection inside",
                description=(
                    "Test correct handling of an empty collection in a nested scatter scenario. "
                    "The workflow first scatters a non-empty collection ([1, 2, 3]) at the top level (scatter1). "
                    "For each item, it attempts a second-level scatter (scatter2) with an empty collection. "
                    "Since scatter2's collection is empty, all downstream actions (reshape, gather2) in that branch "
                    "should be skipped for every item. The test verifies that the skip logic is correctly propagated, "
                    "and that the outer gather (gather1) collects the original items from scatter1, resulting in [1, 2, 3]. "
                    "reshape2 then operates on this result. Only actions in the main execution stream (gather1, reshape2) "
                    "should appear in the final ACTIONS context; skipped actions (reshape, gather2) should not."
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    # Level 1: Scatter a non-empty collection
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
                    ),
                    # Level 2: This scatter is nested and its collection is empty,
                    # so all downstream actions in this branch should be skipped.
                    ActionStatement(
                        ref="scatter2",
                        action="core.transform.scatter",
                        depends_on=["scatter1"],
                        args=ScatterArgs(collection=[]).model_dump(),
                    ),
                    ActionStatement(
                        ref="reshape",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        args={"value": "${{ ACTIONS.scatter2.result }}"},
                    ),
                    # This gather is downstream of the skipped scatter2/reshape,
                    # so it should also be skipped.
                    ActionStatement(
                        ref="gather2",
                        action="core.transform.gather",
                        depends_on=["reshape"],
                        args=GatherArgs(
                            items="${{ ACTIONS.reshape.result }}"
                        ).model_dump(),
                    ),
                    # This gather collects the results from the outer scatter1.
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["gather2"],
                        args=GatherArgs(
                            items="${{ ACTIONS.scatter1.result }}"
                        ).model_dump(),
                    ),
                    # Final reshape, should operate on the result of gather1.
                    ActionStatement(
                        ref="reshape2",
                        action="core.transform.reshape",
                        depends_on=["gather1"],
                        args={"value": "${{ ACTIONS.gather1.result }}"},
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather1": {
                        "result": [1, 2, 3],
                        "result_typename": "list",
                    },  # The outer gather collects the original items.
                    # Only reshape2 is present because reshape (and thus gather2) are skipped.
                    "reshape2": {
                        "result": [1, 2, 3],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="nested-scatter-empty-collection-inside",
        ),
        pytest.param(
            DSLInput(
                title="2D scatter with DAG inside",
                description=(
                    "Test correct handling of a DAG inside an scatter. "
                    "The workflow first scatters a non-empty collection ([1, 2, 3]) at the top level (scatter1). "
                    "For each item, it attempts a second-level scatter (scatter2) with an empty collection. "
                    "Since scatter2's collection is empty, all downstream actions (reshape, gather2) in that branch "
                    "should be skipped for every item."
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(
                            collection=[
                                [1, 2],
                                [3, 4],
                            ]
                        ).model_dump(),
                    ),
                    ActionStatement(
                        ref="scatter2",
                        action="core.transform.scatter",
                        depends_on=["scatter1"],
                        args=ScatterArgs(
                            collection="${{ ACTIONS.scatter1.result }}"
                        ).model_dump(),
                    ),
                    # DAG here, parallel condition handling
                    # ======= Block 1 =======
                    ActionStatement(
                        ref="is_one",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        run_if="${{ ACTIONS.scatter2.result == 1 }}",
                        args={"value": "${{ ACTIONS.scatter2.result }}"},
                    ),
                    ActionStatement(
                        ref="is_two",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        run_if="${{ ACTIONS.scatter2.result == 2 }}",
                        args={"value": "${{ ACTIONS.scatter2.result }}"},
                    ),
                    ActionStatement(
                        ref="is_three",
                        action="core.transform.reshape",
                        depends_on=["scatter2"],
                        run_if="${{ ACTIONS.scatter2.result == 3 }}",
                        args={"value": "${{ ACTIONS.scatter2.result }}"},
                    ),
                    # ======= End Block 1 =======
                    # Block 1 only allows 1, 2, 3 past.
                    # We can apply join_strategy like how we would in global streams.
                    ActionStatement(
                        ref="gather2",
                        action="core.transform.gather",
                        depends_on=["is_one", "is_two", "is_three"],
                        join_strategy=JoinStrategy.ANY,
                        args=GatherArgs(
                            items="${{ ACTIONS.is_one.result || ACTIONS.is_two.result || ACTIONS.is_three.result }}"
                        ).model_dump(),
                    ),
                    # This gather collects the results from the outer scatter1.
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["gather2"],
                        args=GatherArgs(
                            items="${{ ACTIONS.gather2.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather1": {
                        "result": [[1, 2], [3]],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="2d-scatter-dag-inside",
        ),
        # 1D version: single-level scatter with DAG inside, all downstream actions should be skipped if scatter2 is empty
        pytest.param(
            DSLInput(
                title="1D scatter with multiple parallel conditions",
                description=(
                    "Test correct handling of a DAG inside a single-level scatter. "
                    "The workflow scatters a non-empty collection ([1, 2, 3]) at the top level (scatter1). "
                    "For each item, it attempts a second-level scatter (scatter2) with an empty collection. "
                    "Since scatter2's collection is empty, all downstream actions (reshape, gather2) in that branch "
                    "should be skipped for every item."
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2, 3, 4]).model_dump(),
                    ),
                    # DAG here, parallel condition handling
                    ActionStatement(
                        ref="is_one",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        run_if="${{ ACTIONS.scatter1.result == 1 }}",
                        args={"value": "${{ ACTIONS.scatter1.result }}"},
                    ),
                    ActionStatement(
                        ref="is_two",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        run_if="${{ ACTIONS.scatter1.result == 2 }}",
                        args={"value": "${{ ACTIONS.scatter1.result }}"},
                    ),
                    # Join
                    ActionStatement(
                        ref="join",
                        action="core.transform.reshape",
                        depends_on=["is_one", "is_two"],
                        join_strategy=JoinStrategy.ANY,
                        args={
                            "value": "${{ ACTIONS.is_one.result || ACTIONS.is_two.result }}"
                        },
                    ),
                    # This gather collects the results from the outer scatter1.
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["join"],
                        args=GatherArgs(
                            items="${{ ACTIONS.join.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather1": {
                        "result": [1, 2],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="1d-scatter-multi-condition",
        ),
        pytest.param(
            DSLInput(
                title="Errors inside, partition",
                description=(
                    "Gracefully handle errors. With the default error handling,"
                    " we apply the default error handling strategy -- which is to partition"
                    " the results and errors into .result and .error"
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2]).model_dump(),
                    ),
                    # DAG here, parallel condition handling
                    ActionStatement(
                        ref="throw",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        args={"value": "${{ 1/0 }}"},
                    ),
                    # This gather collects the results from the outer scatter1.
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["throw"],
                        args=GatherArgs(
                            items="${{ ACTIONS.throw.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather1": {
                        "result": [],
                        "result_typename": "list",
                        "error": [
                            {
                                "ref": "throw",
                                "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 73",
                                "type": "ExecutorClientError",
                                "expr_context": "ACTIONS",
                                "attempt": 1,
                            },
                            {
                                "ref": "throw",
                                "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 73",
                                "type": "ExecutorClientError",
                                "expr_context": "ACTIONS",
                                "attempt": 1,
                            },
                        ],
                        "error_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="basic-error-handling-partition",
        ),
        pytest.param(
            DSLInput(
                title="Errors inside, drop",
                description=(
                    "Gracefully handle errors. With the default error handling,"
                    " we apply the default error handling strategy -- which is to partition"
                    " the results and errors into .result and .error"
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2]).model_dump(),
                    ),
                    # DAG here, parallel condition handling
                    ActionStatement(
                        ref="throw",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        args={"value": "${{ 1/0 }}"},
                    ),
                    # This gather collects the results from the outer scatter1.
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["throw"],
                        args=GatherArgs(
                            items="${{ ACTIONS.throw.result }}",
                            error_strategy=StreamErrorHandlingStrategy.DROP,
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather1": {
                        "result": [],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="basic-error-handling-drop",
        ),
        pytest.param(
            DSLInput(
                title="Errors inside, include",
                description=(
                    "Gracefully handle errors. With the default error handling,"
                    " we apply the default error handling strategy -- which is to partition"
                    " the results and errors into .result and .error"
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        args=ScatterArgs(collection=[1, 2]).model_dump(),
                    ),
                    # DAG here, parallel condition handling
                    ActionStatement(
                        ref="throw",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        args={"value": "${{ 1/0 }}"},
                    ),
                    # This gather collects the results from the outer scatter1.
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["throw"],
                        args=GatherArgs(
                            items="${{ ACTIONS.throw.result }}",
                            error_strategy=StreamErrorHandlingStrategy.INCLUDE,
                        ).model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "gather1": {
                        "result": [
                            {
                                "ref": "throw",
                                "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 73",
                                "type": "ExecutorClientError",
                                "expr_context": "ACTIONS",
                                "attempt": 1,
                            },
                            {
                                "ref": "throw",
                                "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 73",
                                "type": "ExecutorClientError",
                                "expr_context": "ACTIONS",
                                "attempt": 1,
                            },
                        ],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="basic-error-handling-include",
        ),
        # Test: a -> scatter -> b -> gather, where b accesses a's data
        pytest.param(
            DSLInput(
                title="Hierarchical stream variable lookup: a -> scatter -> b -> gather",
                description=(
                    "Test that a value produced before a scatter can be accessed by a reshape inside the scatter, "
                    "and then gathered. This validates hierarchical stream variable lookup."
                ),
                entrypoint=DSLEntrypoint(ref="gather1"),
                actions=[
                    # a: produce a value
                    ActionStatement(
                        ref="a",
                        action="core.transform.reshape",
                        args={"value": 42},
                    ),
                    # scatter: scatter over a collection
                    ActionStatement(
                        ref="scatter1",
                        action="core.transform.scatter",
                        depends_on=["a"],
                        args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
                    ),
                    # b: inside scatter, access a's value and add to item
                    ActionStatement(
                        ref="b",
                        action="core.transform.reshape",
                        depends_on=["scatter1"],
                        args={
                            "value": "${{ ACTIONS.a.result + ACTIONS.scatter1.result }}"
                        },
                    ),
                    # gather: collect results from b
                    ActionStatement(
                        ref="gather1",
                        action="core.transform.gather",
                        depends_on=["b"],
                        args=GatherArgs(items="${{ ACTIONS.b.result }}").model_dump(),
                    ),
                ],
            ),
            {
                "ACTIONS": {
                    "a": {
                        "result": 42,
                        "result_typename": "int",
                    },
                    "gather1": {
                        "result": [43, 44, 45],
                        "result_typename": "list",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="scope-shadowing-stream-lookup",
        ),
    ],
)
async def test_workflow_scatter_gather(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
    dsl: DSLInput,
    expected: ExecutionContext,
):
    """
    Test that a workflow can scatter a collection.
    """
    test_name = f"{test_workflow_scatter_gather.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        assert result == expected


@pytest.mark.anyio
async def test_workflow_env_and_trigger_access_in_stream(
    test_role: Role, temporal_client: Client, test_worker_factory
) -> None:
    """
    Test that ENV and TRIGGER contexts are accessible from inside a stream.
    The workflow is: scatter -> a -> gather, where 'a' is a reshape returning
    ENV.workflow.execution_id and TRIGGER.data. TRIGGER data is passed in DSLRunArgs.
    """
    # Prepare test TRIGGER data and expected execution_id
    trigger_data = {"foo": "bar", "num": 123}
    test_name = f"{test_workflow_env_and_trigger_access_in_stream.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Define the DSL workflow
    dsl = DSLInput(
        title="ENV and TRIGGER context access in stream",
        description="Test ENV and TRIGGER context access from inside a stream",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
            ),
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={
                    # Return both ENV.workflow.execution_id and TRIGGER.data as a dict
                    "value": "${{ {'exec_id': ENV.workflow.execution_id, 'trigger': TRIGGER} }}"
                },
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["a"],
                args=GatherArgs(items="${{ ACTIONS.a.result }}").model_dump(),
            ),
        ],
    )

    # Prepare expected result
    expected = {
        "ACTIONS": {
            "gather": {
                "result": [
                    {"exec_id": wf_exec_id, "trigger": trigger_data},
                    {"exec_id": wf_exec_id, "trigger": trigger_data},
                    {"exec_id": wf_exec_id, "trigger": trigger_data},
                ],
                "result_typename": "list",
            },
        },
        "INPUTS": {},
        "TRIGGER": trigger_data,
    }

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Run the workflow and check the result
    async with test_worker_factory(temporal_client):
        run_args = DSLRunArgs(
            dsl=dsl,
            role=test_role,
            wf_id=TEST_WF_ID,
            trigger_inputs=trigger_data,
        )
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        assert result == expected


def assert_result_is_run_context(result: dict[str, Any]) -> bool:
    assert isinstance(result, dict), "Result is not a dict"
    assert result.get("wf_id") == str(TEST_WF_ID), "wf_id is not correct"
    assert result.get("wf_exec_id") == generate_test_exec_id(
        "test_workflow_return_strategy"
    ), "wf_exec_id is not correct"
    assert result.get("environment") == "default", "environment is not correct"
    wf_run_id = result.get("wf_run_id")
    assert isinstance(wf_run_id, str) and bool(UUID(wf_run_id)), (
        "wf_run_id is not a UUID"
    )
    return True


@pytest.mark.parametrize(
    "return_strategy,validator",
    [
        # Context strategy returns the full context
        pytest.param(
            "context",
            lambda result: result
            == {
                "ACTIONS": {
                    "a": {
                        "result": 42,
                        "result_typename": "int",
                    },
                },
                "INPUTS": {},
                "TRIGGER": {},
            },
            id="context-strategy",
        ),
        # Minimal strategy returns the RunContext
        pytest.param(
            "minimal",
            assert_result_is_run_context,
            id="minimal-strategy",
        ),
    ],
)
@pytest.mark.anyio
async def test_workflow_return_strategy(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory,
    return_strategy: Literal["context", "minimal"],
    validator: Callable[[dict[str, Any]], bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Test that the workflow return strategy is respected.
    """
    monkeypatch.setenv("TRACECAT__WORKFLOW_RETURN_STRATEGY", return_strategy)
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_RETURN_STRATEGY", return_strategy)
    test_name = f"{test_workflow_return_strategy.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Define the DSL workflow
    dsl = DSLInput(
        title="Workflow return strategy",
        description="Test that the workflow return strategy is respected",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                args={"value": 42},
            ),
        ],
    )

    async with test_worker_factory(temporal_client):
        run_args = DSLRunArgs(
            dsl=dsl,
            role=test_role,
            wf_id=TEST_WF_ID,
        )
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        assert validator(result)
