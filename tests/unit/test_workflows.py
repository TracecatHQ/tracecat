"""Unit tests for workflows.

Objectives
----------
1. Test that the workflows can be executed in an isolated environment
2. Test that the workflows can be executed with the correct inputs and outputs
3. Test that the workflows can be executed with the correct ordering

"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from tests.shared import DSL_UTILITIES, TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Workflow
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.models import DSLConfig
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLActivities, DSLContext, DSLWorkflow, retry_policies
from tracecat.expressions.shared import ExprContext
from tracecat.logger import logger
from tracecat.secrets.models import CreateSecretParams, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService


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
    config = DSLConfig(enable_runtime_tests=True, environment="default")
    logger.info(f"Runtime config: {config}")
    return config


@pytest.mark.parametrize(
    "dsl",
    ["shared_adder_tree", "shared_kite", "shared_tree"],
    indirect=True,
    ids=lambda x: x.title,
)
@pytest.mark.asyncio
async def test_workflow_can_run_from_yaml(dsl, temporal_cluster, test_role):
    test_name = f"test_workflow_can_run_from_yaml-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    client = await get_temporal_client()
    # Run workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load() + DSL_UTILITIES,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    logger.info(result)
    assert len(result[ExprContext.ACTIONS]) == len(dsl.actions)


def assert_respectful_exec_order(dsl: DSLInput, final_context: DSLContext):
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
    ids=lambda x: x.title,
)
@pytest.mark.asyncio
async def test_workflow_ordering_is_correct(
    dsl, temporal_cluster, test_role, temporal_client, base_registry
):
    """We need to test that the ordering of the workflow tasks is correct."""

    # Connect client
    test_name = f"test_workflow_ordering_is_correct-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Run a worker for the activities and workflow
    async with Worker(
        temporal_client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load() + DSL_UTILITIES,
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
        "unit_runtime_test_adder_tree",
        "unit_runtime_test_chain",
        "unit_transform_filter_dict",
        "unit_transform_filter_function",
    ],
    indirect=True,
    ids=lambda x: x.title,
)
@pytest.mark.asyncio
async def test_workflow_completes_and_correct(
    dsl_with_expected, temporal_cluster, test_role, runtime_config, base_registry
):
    dsl, expected = dsl_with_expected
    test_name = f"test_correctness_execution-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load() + DSL_UTILITIES,
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


@pytest.mark.parametrize(
    "dsl", ["stress_adder_tree"], indirect=True, ids=lambda x: x.title
)
@pytest.mark.slow
@pytest.mark.asyncio
async def test_stress_workflow(dsl, temporal_cluster, test_role, base_registry):
    """Test that we can have multiple executions of the same workflow running at the same time."""
    test_name = f"test_stress_workflow-{dsl.title}"
    client = await get_temporal_client()

    tasks: list[asyncio.Task] = []
    async with (
        Worker(
            client,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            activities=DSLActivities.load() + DSL_UTILITIES,
            workflows=[DSLWorkflow],
            workflow_runner=new_sandbox_runner(),
        ),
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


@pytest.mark.asyncio
async def test_workflow_set_environment_correct(
    temporal_cluster, test_role, temporal_client, base_registry
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
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=DSLActivities.load() + DSL_UTILITIES,
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


@pytest.mark.asyncio
async def test_workflow_override_environment_correct(
    temporal_cluster, test_role, temporal_client, base_registry
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
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=DSLActivities.load() + DSL_UTILITIES,
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


@pytest.mark.asyncio
async def test_workflow_default_environment_correct(
    temporal_cluster, test_role, temporal_client, base_registry
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
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=DSLActivities.load() + DSL_UTILITIES,
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


async def _create_and_commit_workflow(dsl: DSLInput, role: Role) -> Workflow:
    async with get_async_session_context_manager() as session:
        # Create the child workflow
        mgmt_service = WorkflowsManagementService(session, role=role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        if not workflow:
            return pytest.fail("Workflow wasn't created")
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
        activities=DSLActivities.load() + DSL_UTILITIES,
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


@pytest.mark.asyncio
async def test_child_workflow_success(
    temporal_cluster, test_role, temporal_client, base_registry
):
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


@pytest.mark.asyncio
async def test_child_workflow_context_passing(
    temporal_cluster, test_role, temporal_client, base_registry
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
            # "config": {"enable_runtime_tests": False, "scheduler": "dynamic"},
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


@pytest.mark.asyncio
async def test_single_child_workflow_override_environment_correct(
    temporal_cluster, test_role, temporal_client, base_registry
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


@pytest.mark.asyncio
async def test_multiple_child_workflow_override_environment_correct(
    temporal_cluster, test_role, temporal_client, base_registry
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


@pytest.mark.asyncio
async def test_single_child_workflow_environment_has_correct_default(
    temporal_cluster, test_role, temporal_client, base_registry
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


@pytest.mark.asyncio
async def test_multiple_child_workflow_environments_have_correct_defaults(
    temporal_cluster, test_role, temporal_client, base_registry
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


@pytest.mark.asyncio
async def test_single_child_workflow_get_correct_secret_environment(
    temporal_cluster,
    test_role,
    temporal_client,
    base_registry,
    monkeysession: pytest.MonkeyPatch,
):
    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)
    test_name = f"{test_single_child_workflow_get_correct_secret_environment.__name__}"
    test_description = "Test that a single child workflow can get a secret from the correect environment"

    # Add secrets to the db
    async with SecretsService.with_session(role=test_role) as service:
        await service.create_secret(
            CreateSecretParams(
                name="test_single_child_workflow_get_correct_secret_environment",
                environment="__FIRST__",
                keys=[SecretKeyValue(key="KEY", value="FIRST_VALUE")],
            )
        )
        await service.create_secret(
            CreateSecretParams(
                name="test_single_child_workflow_get_correct_secret_environment",
                environment="__SECOND__",
                keys=[SecretKeyValue(key="KEY", value="SECOND_VALUE")],
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


@pytest.mark.asyncio
async def test_pull_based_workflow_fetches_latest_version(
    temporal_client, test_role, base_registry
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
