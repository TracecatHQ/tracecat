"""Unit tests for workflows.

Objectives
----------
1. Test that the workflows can be executed in an isolated environment
2. Test that the workflows can be executed with the correct inputs and outputs
3. Test that the workflows can be executed with the correct ordering

"""

import os
import re
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import pytest
import yaml

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]
from pydantic import SecretStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.api.enums.v1 import EventType
from temporalio.api.enums.v1.workflow_pb2 import ParentClosePolicy
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id, to_data, to_inline
from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Schedule, Workflow
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
from tracecat.dsl.schemas import (
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    GatherArgs,
    RunActionInput,
    ScatterArgs,
    TaskResult,
)
from tracecat.dsl.types import ActionErrorInfoAdapter
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import (
    WF_EXEC_ID_PATTERN,
    WorkflowExecutionID,
    WorkflowID,
    WorkflowUUID,
)
from tracecat.logger import logger
from tracecat.pagination import CursorPaginationParams
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.storage.object import (
    CollectionObject,
    InlineObject,
    StoredObject,
    StoredObjectValidator,
)
from tracecat.storage.utils import (
    resolve_execution_context as resolve_exec_ctx_from_dict,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService
from tracecat.variables.schemas import VariableCreate
from tracecat.variables.service import VariablesService
from tracecat.workflow.executions.enums import WorkflowEventType
from tracecat.workflow.executions.schemas import (
    EventGroup,
    WorkflowExecutionEvent,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate

type WorkerFactory = Callable[[Client], Worker]


# Add a fixture that checks if we are running locally.
# If we are, make it shut down executor and worker processes (docker)
@pytest.fixture(autouse=True, scope="session")
def shutdown_executor_and_worker(monkeysession: pytest.MonkeyPatch):
    # In CI, do nothing - executor/worker are not running as separate containers
    if os.environ.get("GITHUB_ACTIONS") is not None:
        return
    monkeysession.setattr(config, "TRACECAT__API_URL", "http://localhost/api")
    # shutdown executor and worker processes
    import subprocess

    # grab prefixes from COMPOSE_PROJECT_NAME
    compose_project_name = os.environ.get("COMPOSE_PROJECT_NAME")
    if compose_project_name is None:
        pytest.skip("COMPOSE_PROJECT_NAME is not set")
    worker_prefix = f"{compose_project_name}-worker"
    executor_prefix = f"{compose_project_name}-executor"
    out = subprocess.run(
        ["docker", "compose", "down", "-v", worker_prefix, executor_prefix],
        capture_output=True,
    )
    if out.returncode != 0:
        pytest.skip(
            f"Failed to shut down executor and worker processes: {out.stderr.decode('utf-8')}"
        )
    logger.info("Shut down executor and worker processes")


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


ExecutionContextTA = TypeAdapter(ExecutionContext)


def wrap_with_envelopes(expected: dict[str, Any]) -> ExecutionContext:
    """Wrap expected values with StoredObject envelope for comparison.

    Transforms raw YAML expected values into ExecutionContext format by wrapping
    result values and TRIGGER in InlineObject envelopes.
    """
    transformed: dict[str, Any] = {}

    # Wrap ACTIONS results in InlineObject envelopes
    if "ACTIONS" in expected:
        transformed["ACTIONS"] = {}
        for action_ref, task_result in expected["ACTIONS"].items():
            transformed["ACTIONS"][action_ref] = TaskResult.from_result(
                result=task_result.get("result")
            )

    # Wrap TRIGGER in InlineObject envelope
    transformed["TRIGGER"] = (
        InlineObject(data=expected.get("TRIGGER")) if expected.get("TRIGGER") else None
    )

    return ExecutionContextTA.validate_python(transformed)


def dict_to_exec_context(expected_dict: dict[str, Any]) -> ExecutionContext:
    """Convert a dictionary to ExecutionContext, preserving result_typename.

    This helper function converts test case dictionaries to ExecutionContext objects,
    preserving the explicit result_typename values specified in test cases.
    """
    normalized = normalize_error_line_numbers(expected_dict)
    actions: dict[str, TaskResult] = {}
    for ref, task_result in normalized.get("ACTIONS", {}).items():
        result_value = task_result.get("result")
        if isinstance(result_value, dict) and result_value.get("type") in (
            "inline",
            "external",
            "collection",
        ):
            result_obj = StoredObjectValidator.validate_python(result_value)
        else:
            result_obj = InlineObject(data=result_value)

        actions[ref] = TaskResult(
            result=result_obj,
            result_typename=task_result.get("result_typename", "unknown"),
            error=task_result.get("error"),
            error_typename=task_result.get("error_typename"),
            interaction=task_result.get("interaction"),
            interaction_id=task_result.get("interaction_id"),
            interaction_type=task_result.get("interaction_type"),
            collection_index=task_result.get("collection_index"),
        )
    return ExecutionContext(
        ACTIONS=actions,
        TRIGGER=InlineObject(data=normalized.get("TRIGGER"))
        if normalized.get("TRIGGER")
        else None,
    )


async def resolve_execution_context(ctx: ExecutionContext) -> ExecutionContext:
    """Resolve the execution context by unwrapping the StoredObjects."""
    validated = ExecutionContextTA.validate_python(ctx)
    for action_ref, task_result in validated["ACTIONS"].items():
        data = await to_data(task_result.result)
        try:
            # If data is a nested execution context, resolve it
            nested_validated = ExecutionContextTA.validate_python(data)
            data = await resolve_execution_context(nested_validated)
        except ValidationError:
            pass

        validated["ACTIONS"][action_ref] = task_result.model_copy(
            update={"result": InlineObject(data=data)}
        )
    if validated["TRIGGER"] is not None:
        validated["TRIGGER"] = await to_inline(validated["TRIGGER"])
    return validated


@pytest.mark.anyio
async def test_dict_to_exec_context_preserves_error_metadata() -> None:
    ctx = dict_to_exec_context(
        {
            "ACTIONS": {
                "a": {
                    "result": 1,
                    "result_typename": "int",
                    "error": {"message": "oops"},
                    "error_typename": "dict",
                }
            },
            "TRIGGER": {},
        }
    )

    task = ctx["ACTIONS"]["a"]
    assert await to_data(task.result) == 1
    assert task.result_typename == "int"
    assert task.error == {"message": "oops"}
    assert task.error_typename == "dict"


@pytest.mark.anyio
async def test_resolve_execution_context_preserves_task_result_metadata() -> None:
    ctx: ExecutionContext = ExecutionContext(
        ACTIONS={
            "a": TaskResult(
                result=InlineObject(data={"value": 1}),
                result_typename="CustomType",
                error={"message": "oops"},
                error_typename="dict",
                interaction={"kind": "test"},
                interaction_id="interaction_1",
                interaction_type="test",
                collection_index=0,
            )
        },
        TRIGGER=InlineObject(data={"trigger": True}),
    )

    resolved = await resolve_execution_context(ctx)
    task = resolved["ACTIONS"]["a"]

    assert await to_data(task.result) == {"value": 1}
    assert task.result_typename == "CustomType"
    assert task.error == {"message": "oops"}
    assert task.error_typename == "dict"
    assert task.interaction == {"kind": "test"}
    assert task.interaction_id == "interaction_1"
    assert task.interaction_type == "test"
    assert task.collection_index == 0


async def assert_context_equal(result: StoredObject, expected: ExecutionContext):
    """Assert that the actual execution context is equal to the expected execution context."""
    typed_result = await to_inline_exec_context(result)
    resolved = await resolve_execution_context(typed_result.data)
    assert resolved == expected


@pytest.fixture
def dsl_with_expected(
    request: pytest.FixtureRequest,
) -> tuple[DSLInput, ExecutionContext]:
    test_name = request.param
    data_path = Path("tests/data/workflows") / f"{test_name}.yml"
    expected_path = Path("tests/data/workflows") / f"{test_name}_expected.yml"
    dsl = DSLInput.from_yaml(data_path)
    expected = load_expected_dsl_output(expected_path)
    # Transform expected to dict[str, TaskResult]
    return dsl, wrap_with_envelopes(expected)


def load_expected_dsl_output(path: Path) -> dict[str, Any]:
    with path.open() as f:
        yaml_data = f.read()
    data = yaml.safe_load(yaml_data)
    return {key: (value or {}) for key, value in data.items()}


def _normalize_error_message(msg: str) -> str:
    """Normalize an error message string for comparison."""
    # Remove the entire debug info section (File/Function/Line) which is only present in dev mode
    # This section starts with dashes and includes file path, function name, and line number
    msg = re.sub(r"\n*-{20,}\n.*", "", msg, flags=re.DOTALL)
    # Normalize newlines (collapse multiple newlines, normalize line endings)
    msg = re.sub(r"\r\n", "\n", msg)
    msg = re.sub(r"\n{2,}", "\n\n", msg)
    return msg


def _normalize_url(url: str) -> str:
    """Normalize URL by removing default ports."""
    # Remove :80 for http and :443 for https
    url = re.sub(r"(http://[^/:]+):80(/|$)", r"\1\2", url)
    url = re.sub(r"(https://[^/:]+):443(/|$)", r"\1\2", url)
    return url


def normalize_error_line_numbers(obj: Any) -> Any:
    """Recursively normalize error messages and URLs for comparison.

    This prevents test flakiness when source code line numbers change,
    newline formatting varies, or URLs include default ports.
    """
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "message" and isinstance(v, str):
                result[k] = _normalize_error_message(v)
            elif k in ("orig_wf_exec_url",) and isinstance(v, str):
                result[k] = _normalize_url(v)
            else:
                result[k] = normalize_error_line_numbers(v)
        return result
    elif isinstance(obj, list):
        return [normalize_error_line_numbers(item) for item in obj]
    return obj


@pytest.fixture
def runtime_config() -> DSLConfig:
    config = DSLConfig(environment="default")
    logger.info(f"Runtime config: {config}")
    return config


def raw_data_to_exec_context(raw_data: dict[str, Any]) -> ExecutionContext:
    """Convert raw workflow data to ExecutionContext by wrapping values in InlineObject."""
    actions: dict[str, TaskResult] = {}
    for ref, task_result in raw_data.get("ACTIONS", {}).items():
        result_value = task_result.get("result")
        # Wrap the result value in InlineObject if not already wrapped
        if isinstance(result_value, dict) and result_value.get("type") in (
            "inline",
            "external",
            "collection",
        ):
            # Already a StoredObject (InlineObject, ExternalObject, or CollectionObject)
            result_obj = StoredObjectValidator.validate_python(result_value)
        else:
            result_obj = InlineObject(data=result_value)
        actions[ref] = TaskResult(
            result=result_obj,
            result_typename=task_result.get("result_typename", "unknown"),
            error=task_result.get("error"),
            error_typename=task_result.get("error_typename"),
            interaction=task_result.get("interaction"),
            interaction_id=task_result.get("interaction_id"),
            interaction_type=task_result.get("interaction_type"),
            collection_index=task_result.get("collection_index"),
        )
    trigger_data = raw_data.get("TRIGGER")
    trigger: StoredObject | None = None
    if trigger_data is not None and trigger_data != {}:
        if isinstance(trigger_data, dict) and trigger_data.get("type") in (
            "inline",
            "external",
            "collection",
        ):
            trigger = StoredObjectValidator.validate_python(trigger_data)
        else:
            trigger = InlineObject(data=trigger_data)
    return ExecutionContext(ACTIONS=actions, TRIGGER=trigger)


async def to_inline_exec_context(obj: StoredObject) -> InlineObject[ExecutionContext]:
    """Convert a StoredObject to an InlineObject[ExecutionContext]."""
    try:
        inlined = await to_inline(obj)
        # Convert raw data to ExecutionContext by wrapping values
        exec_context = raw_data_to_exec_context(inlined.data)
        return InlineObject(data=exec_context)
    except Exception as e:
        pytest.fail(
            f"Failed to convert StoredObject to InlineObject[ExecutionContext]: {e}"
        )


@pytest.mark.parametrize(
    "dsl",
    ["shared_adder_tree", "shared_kite", "shared_tree"],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.anyio
async def test_workflow_can_run_from_yaml(
    dsl: DSLInput,
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    test_name = f"test_workflow_can_run_from_yaml-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    role = ctx_role.get()
    assert role is not None
    # Run workflow
    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=role, wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    typed_result = await to_inline_exec_context(result)
    assert len(typed_result.data["ACTIONS"]) == len(dsl.actions)


async def assert_respectful_exec_order(dsl: DSLInput, final_context: ExecutionContext):
    act_outputs = final_context["ACTIONS"]
    for action in dsl.actions:
        target = action.ref
        for source in action.depends_on:
            # Results come back as dicts when deserialized through Temporal
            source_task = TaskResult.model_validate(act_outputs[source])
            target_task = TaskResult.model_validate(act_outputs[target])
            # Materialize StoredObject values for ordering assertions.
            source_data = await to_data(source_task.result)
            target_data = await to_data(target_task.result)
            assert source_data < target_data


@pytest.mark.parametrize(
    "dsl",
    ["unit_ordering_kite", "unit_ordering_kite2"],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.anyio
async def test_workflow_ordering_is_correct(
    dsl: DSLInput,
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    """We need to test that the ordering of the workflow tasks is correct."""

    # Connect client
    test_name = f"test_workflow_ordering_is_correct-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    role = ctx_role.get()
    assert role is not None

    # Run a worker for the activities and workflow
    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=role, wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    # Iterate over the actual ordering of the tasks
    # and compare that in the topological ordering every LHS task in a pair executed before the RHS task

    # Check that the execution order respects the graph edges
    typed_result = await to_inline_exec_context(result)
    await assert_respectful_exec_order(dsl, typed_result.data)


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
    dsl_with_expected: tuple[DSLInput, ExecutionContext],
    test_role: Role,
    runtime_config: DSLConfig,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    dsl, expected = dsl_with_expected
    test_name = f"test_correctness_execution-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    role = ctx_role.get()
    assert role is not None

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with test_worker_factory(client), test_executor_worker_factory(client):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=role,
                wf_id=TEST_WF_ID,
                runtime_config=runtime_config,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.exceptions.TracecatExpressionErrorTracecatValidationError"
                ],
            ),
        )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_workflow_set_environment_correct(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    assert await to_data(result) == "__TEST_ENVIRONMENT__"


@pytest.mark.anyio
async def test_workflow_override_environment_correct(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    # Materialize StoredObject result for comparison.
    assert await to_data(result) == "__CORRECT_ENVIRONMENT__"


@pytest.mark.anyio
async def test_workflow_default_environment_correct(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    assert await to_data(result) == "default"


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
            workflow_id=workflow_id, dsl=constructed_dsl, alias=alias
        )
        return workflow


async def _run_workflow(
    wf_exec_id: str,
    run_args: DSLRunArgs,
    worker: Worker,
    executor_worker: Worker | None = None,
):
    if executor_worker:
        async with worker, executor_worker:
            result = await worker.client.execute_workflow(
                DSLWorkflow.run,
                run_args,
                id=wf_exec_id,
                task_queue=worker.task_queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            )
    else:
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
async def test_child_workflow_success(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
                    "for_each": "${{ for var.x in [1, 2, 3, 4, 5, 6, 7] }}",
                    "args": {
                        "workflow_id": child_workflow.id,
                        "trigger_inputs": {
                            "number": "${{ var.x }}",  # This is the parent's trigger data
                        },
                    },
                    "depends_on": [],
                },
            ],
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    expected = ExecutionContext(
        ACTIONS={
            "parent": TaskResult(
                result=InlineObject(data=[1001, 1002, 1003, 1004, 1005, 1006, 1007]),
                result_typename="list",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_child_workflow_context_passing(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
            "returns": None,
            "tests": [],
            "triggers": [],
        }
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=parent_workflow_id,
        trigger_inputs=InlineObject(
            data={
                "data": "__EXPECTED_DATA__",
            }
        ),
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    # Parent expected
    # The child workflow's context has InlineObject envelopes
    expected = ExecutionContext(
        ACTIONS={
            "parent_first_action": TaskResult(
                result=InlineObject(
                    data={
                        "reshaped_data": "__EXPECTED_DATA__",
                    }
                ),
                result_typename="dict",
            ),
            "parent_second_action": TaskResult(
                result=InlineObject(
                    data=ExecutionContext(
                        ACTIONS={
                            "reshape_parent_data": TaskResult(
                                result=InlineObject(
                                    data={
                                        "parent_data": "Parent sent child __EXPECTED_DATA__"
                                    }
                                ),
                                result_typename="dict",
                            )
                        },
                        TRIGGER=InlineObject(
                            data={
                                "data_from_parent": "Parent sent child __EXPECTED_DATA__"
                            }
                        ),
                    )
                ),
                result_typename="dict",
            ),
        },
        TRIGGER=InlineObject(data={"data": "__EXPECTED_DATA__"}),
    )
    await assert_context_equal(result, expected)


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
    request: pytest.FixtureRequest,
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    loop_strategy: LoopStrategy,
    loop_kwargs: dict[str, Any],
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
):
    # Setup
    test_name = f"{test_child_workflow_loop.__name__}_{request.node.callspec.id}"
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
        returns=None,
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
        trigger_inputs=InlineObject(
            data={
                "data": "__EXPECTED_DATA__",
            }
        ),
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    # Parent expected
    expected = ExecutionContext(
        ACTIONS={
            "run_child": TaskResult(
                result=InlineObject(
                    data=[
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
                ),
                result_typename="list",
            ),
        },
        TRIGGER=InlineObject(data={"data": "__EXPECTED_DATA__"}),
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_child_workflow_parallel_loop_with_dispatch_cap(
    test_role: Role,
    temporal_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
) -> None:
    test_name = test_child_workflow_parallel_loop_with_dispatch_cap.__name__
    wf_exec_id = generate_test_exec_id(test_name)
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW", 8)

    child_dsl = DSLInput(
        entrypoint=DSLEntrypoint(expects={}, ref="reshape"),
        actions=[
            ActionStatement(
                ref="reshape",
                action="core.transform.reshape",
                args={
                    "value": {
                        "index": "${{ TRIGGER.index }}",
                    },
                },
                start_delay=1,
            )
        ],
        description="Testing bounded child workflow fanout",
        returns="${{ ACTIONS.reshape.result }}",
        title="Child",
        triggers=[],
    )
    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)

    parent_dsl = DSLInput(
        title="Parent",
        description="Test bounded child fanout",
        entrypoint=DSLEntrypoint(ref="run_child", expects={}),
        actions=[
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={
                    "workflow_id": child_workflow.id,
                    "trigger_inputs": {"index": "${{ var.x }}"},
                    "loop_strategy": LoopStrategy.PARALLEL.value,
                },
                for_each="${{ for var.x in FN.range(0, 12) }}",
            ),
        ],
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
    )
    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    assert await to_data(result) == [{"index": i} for i in range(12)]


# Test workflow alias
@pytest.mark.anyio
async def test_single_child_workflow_alias(
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
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
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
    )
    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    # Parent expected - unwrap StoredObject to compare actual data
    assert await to_data(result) == {"data": "Test", "index": 0}


@pytest.mark.anyio
async def test_child_workflow_alias_not_found_surfaces_detail(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    test_name = test_child_workflow_alias_not_found_surfaces_detail.__name__
    wf_exec_id = generate_test_exec_id(test_name)
    missing_alias = "missing_child_alias_for_test"

    parent_dsl = DSLInput(
        title="Parent",
        description="Test missing child workflow alias surfaces useful error",
        entrypoint=DSLEntrypoint(ref="run_child", expects={}),
        actions=[
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={
                    "workflow_alias": missing_alias,
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
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    with pytest.raises(WorkflowFailureError) as exc_info:
        _ = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    assert str(exc_info.value) == "Workflow execution failed"
    cause = exc_info.value.cause
    if isinstance(cause, ActivityError):
        nested = cause.cause
        if isinstance(nested, BaseException):
            cause = nested
    assert isinstance(cause, ApplicationError)
    err = str(cause)
    assert f"Workflow alias '{missing_alias}' not found" in err
    assert "Activity task failed" not in err


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
    request: pytest.FixtureRequest,
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    alias: str,
    loop_strategy: LoopStrategy,
    loop_kwargs: dict[str, Any],
    test_worker_factory: Callable[[Client], Worker],
    test_executor_worker_factory: Callable[[Client], Worker],
):
    """Test that child workflows can be executed using aliases."""
    test_name = (
        f"{test_child_workflow_alias_with_loop.__name__}_{request.node.callspec.id}"
    )
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
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )
    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
        trigger_inputs=InlineObject(data="__EXPECTED_DATA__"),
    )
    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    # Parent expected
    assert await to_data(result) == [
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
async def test_child_workflow_with_expression_alias(
    test_role: Role,
    temporal_client: Client,
    child_dsl: DSLInput,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    """Test that child workflows can be executed using expression-based aliases."""
    test_name = test_child_workflow_with_expression_alias.__name__
    wf_exec_id = generate_test_exec_id(test_name)

    # Create child workflow with an alias
    child_workflow = await _create_and_commit_workflow(
        child_dsl, test_role, alias="expression_child"
    )

    assert child_workflow.alias == "expression_child"

    # Parent workflow that resolves the alias from an expression
    parent_dsl = DSLInput(
        title="Parent",
        description="Test parent workflow can resolve child alias from expression",
        entrypoint=DSLEntrypoint(ref="get_alias", expects={}),
        actions=[
            ActionStatement(
                ref="get_alias",
                action="core.transform.reshape",
                args={
                    "value": "expression_child",  # This will be the alias
                },
                depends_on=[],
                description="",
                for_each=None,
                run_if=None,
            ),
            ActionStatement(
                ref="run_child",
                action="core.workflow.execute",
                args={
                    "workflow_alias": "${{ ACTIONS.get_alias.result }}",  # Use expression
                    "trigger_inputs": {
                        "data": "Test data",
                        "index": 42,
                    },
                },
                depends_on=["get_alias"],
                description="",
                for_each=None,
                run_if=None,
            ),
        ],
        returns="${{ ACTIONS.run_child.result }}",
        triggers=[],
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000002"),
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    # Verify the child workflow was called correctly
    assert await to_data(result) == {"data": "Test data", "index": 42}


@pytest.mark.anyio
async def test_single_child_workflow_override_environment_correct(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    expected = ExecutionContext(
        ACTIONS={
            "parent": TaskResult(
                result=InlineObject(data="__TEST_ENVIRONMENT__"),
                result_typename="str",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_multiple_child_workflow_override_environment_correct(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    expected = ExecutionContext(
        ACTIONS={
            "parent": TaskResult(
                result=InlineObject(
                    data=["prod", "dev", "staging", "client1", "client2"]
                ),
                result_typename="list",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_single_child_workflow_environment_has_correct_default(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    expected = ExecutionContext(
        ACTIONS={
            "parent": TaskResult(
                result=InlineObject(data="__TESTING_DEFAULT__"),
                result_typename="str",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_multiple_child_workflow_environments_have_correct_defaults(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    expected = ExecutionContext(
        ACTIONS={
            "parent": TaskResult(
                result=InlineObject(
                    data=[
                        "__TESTING_DEFAULT__ 1",
                        "__TESTING_DEFAULT__ 2",
                        "__TESTING_DEFAULT__ 3",
                    ]
                ),
                result_typename="list",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_single_child_workflow_get_correct_secret_environment(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    expected = ExecutionContext(
        ACTIONS={
            "parent": TaskResult(
                result=InlineObject(
                    data=[
                        "KEY is FIRST_VALUE",
                        "KEY is SECOND_VALUE",
                    ]
                ),
                result_typename="list",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_workflow_can_access_workspace_variables(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
    """Test that workflows can access workspace variables via VARS context."""
    test_name = f"{test_workflow_can_access_workspace_variables.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Create workspace variables

    async with VariablesService.with_session(role=test_role) as service:
        # Create a simple configuration variable
        await service.create_variable(
            VariableCreate(
                name="test_config",
                description="Test configuration for workflow",
                values={
                    "api_url": "https://api.example.com",
                    "timeout": 30,
                    "max_retries": 3,
                },
                environment="default",
            )
        )
        # Create another variable with nested data
        await service.create_variable(
            VariableCreate(
                name="test_settings",
                description="Test settings",
                values={
                    "enabled": True,
                    "threshold": 100,
                },
                environment="default",
            )
        )

    # Create DSL that uses workspace variables
    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "use_vars"},
            "actions": [
                {
                    "ref": "use_vars",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "api_url": "${{ VARS.test_config.api_url }}",
                            "timeout": "${{ VARS.test_config.timeout }}",
                            "max_retries": "${{ VARS.test_config.max_retries }}",
                            "enabled": "${{ VARS.test_settings.enabled }}",
                            "threshold": "${{ VARS.test_settings.threshold }}",
                        }
                    },
                    "depends_on": [],
                    "description": "Test accessing workspace variables",
                }
            ],
            "description": "Test workflow with workspace variables",
            "returns": "${{ ACTIONS.use_vars.result }}",
            "tests": [],
            "title": test_name,
            "triggers": [],
        }
    )

    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    # Verify the workflow can access all workspace variables
    expected = {
        "api_url": "https://api.example.com",
        "timeout": 30,
        "max_retries": 3,
        "enabled": True,
        "threshold": 100,
    }

    assert await to_data(result) == expected


@pytest.mark.anyio
async def test_pull_based_workflow_fetches_latest_version(
    temporal_client: Client,
    test_role: Role,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(
        f"{wf_exec_id}:first", run_args, worker, executor_worker
    )

    assert await to_data(result) == "__EXPECTED_FIRST_RESULT__"

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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(
        f"{wf_exec_id}:second", run_args, worker, executor_worker
    )
    assert await to_data(result) == "__EXPECTED_SECOND_RESULT__"


@pytest.mark.anyio
async def test_scheduled_workflow_legacy_role_auto_heals_organization_id(
    temporal_client: Client,
    test_role: Role,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = test_role.workspace_id
    if workspace_id is None:
        pytest.fail("test_role.workspace_id is required")

    # Keep this test self-contained when local env vars are missing.
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setenv("TRACECAT__LOCAL_REPOSITORY_ENABLED", "1")
    monkeypatch.setattr(config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True)
    minio_access_key = (
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minio"
    )
    minio_secret_key = (
        os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "password"
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", minio_access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", minio_secret_key)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "echo"},
            "actions": [
                {
                    "ref": "echo",
                    "action": "core.transform.reshape",
                    "args": {"value": "${{ TRIGGER.legacy_payload }}"},
                    "depends_on": [],
                    "description": "",
                }
            ],
            "description": "Scheduled workflow legacy role auto-heal test",
            "returns": "${{ ACTIONS.echo.result }}",
            "tests": [],
            "title": "Legacy schedule role healing",
            "triggers": [],
        }
    )
    workflow = await _create_and_commit_workflow(dsl, test_role)
    workflow_id = WorkflowUUID.new(workflow.id)

    payload_value = "__LEGACY_SCHEDULE_PAYLOAD__"
    async with get_async_session_context_manager() as session:
        schedule = Schedule(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            inputs={"legacy_payload": payload_value},
            every=timedelta(minutes=30),
            status="online",
        )
        session.add(schedule)
        await session.commit()
        await session.refresh(schedule)

    legacy_schedule_role = Role(
        type="service",
        service_id="tracecat-schedule-runner",
        workspace_id=workspace_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-schedule-runner"],
    )
    run_args = DSLRunArgs(
        role=legacy_schedule_role,
        wf_id=workflow_id,
        schedule_id=ScheduleUUID.new(schedule.id),
    )
    wf_exec_id = generate_test_exec_id(
        test_scheduled_workflow_legacy_role_auto_heals_organization_id.__name__
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    assert await to_data(result) == payload_value

    handle = temporal_client.get_workflow_handle(wf_exec_id)
    activity_names: list[str] = []
    async for event in handle.fetch_history_events():
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            attrs = event.activity_task_scheduled_event_attributes
            activity_names.append(attrs.activity_type.name)

    assert "get_workspace_organization_id_activity" in activity_names
    assert "get_schedule_trigger_inputs_activity" in activity_names


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
    ),
    "type": "ExecutionError",
    "expr_context": "ACTIONS",
    "attempt": 1,
    "stream_id": "<root>:0",
    "children": None,
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
    test_role: Role,
    runtime_config: DSLConfig,
    dsl_data: dict[str, Any],
    expected: dict[str, Any],
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    dsl = DSLInput(**dsl_data)
    test_name = f"test_workflow_error-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with test_worker_factory(client), test_executor_worker_factory(client):
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
            # Keep enough headroom for first-run executor environment setup in CI.
            run_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.exceptions.TracecatExpressionErrorTracecatValidationError"
                ],
            ),
        )
        data = await to_data(result)
        approximately_equal(data, expected)


@pytest.mark.anyio
async def test_workflow_join_unreachable(
    test_role, runtime_config, test_worker_factory, test_executor_worker_factory
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

    async with test_worker_factory(client), test_executor_worker_factory(client):
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
                run_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=1,
                    non_retryable_error_types=[
                        "tracecat.exceptions.TracecatExpressionError",
                        "TracecatValidationError",
                    ],
                ),
            )


@pytest.mark.anyio
async def test_workflow_multiple_entrypoints(
    test_role, runtime_config, test_worker_factory, test_executor_worker_factory
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

    async with test_worker_factory(client), test_executor_worker_factory(client):
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
            run_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.exceptions.TracecatExpressionError",
                    "TracecatValidationError",
                ],
            ),
        )
    assert await to_data(result) == {
        "first": "ENTRYPOINT_1",
        "second": "ENTRYPOINT_2",
        "third": "ENTRYPOINT_3",
    }


@pytest.mark.anyio
async def test_workflow_runs_template_for_each(
    test_role: Role,
    runtime_config: DSLConfig,
    temporal_client: Client,
    db_session_with_repo: tuple[AsyncSession, uuid.UUID],
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    from tracecat.registry.actions.schemas import (
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

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
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
            run_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=1,
                non_retryable_error_types=[
                    "tracecat.exceptions.TracecatExpressionError",
                    "TracecatValidationError",
                ],
            ),
        )
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    assert data == [101, 102, 103, 104, 105]


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
            workflow_id=workflow_id, dsl=constructed_dsl, alias=alias
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

    # Use normalize_error_line_numbers on both sides to avoid flaky assertions when
    # line numbers change, newline formatting varies, or URLs include default ports
    # Extract .data from InlineObject envelope for comparison
    trigger_data = (
        group.action_input.trigger_inputs.data
        if isinstance(group.action_input.trigger_inputs, InlineObject)
        else group.action_input.trigger_inputs
    )
    assert normalize_error_line_numbers(trigger_data) == normalize_error_line_numbers(
        {
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
                        "Line: 77"
                    ),
                    "ref": "failing_action",
                    "type": "ExecutionError",
                    "stream_id": "<root>:0",
                    "children": None,
                }
            ],
            "handler_wf_id": str(WorkflowUUID.new(handler_wf.id)),
            "message": (
                "Workflow failed with 1 error(s)\n\n"
                f"{'=' * 10} (1/1) ACTIONS.failing_action {'=' * 10}\n\n"
                "ExecutionError: [ACTIONS.failing_action -> execute_action] (Attempt 1)\n\n"
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
                "Line: 77"
            ),
            "orig_wf_exec_id": failing_wf_exec_id,
            "orig_wf_exec_url": wf_exec_url,
            "orig_wf_title": "Division by zero",
            "trigger_type": "manual",
            "orig_wf_id": str(failing_wf_id),
        }
    )
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
    request: pytest.FixtureRequest,
    test_role: Role,
    temporal_client: Client,
    mode: Literal["id", "alias"],
    error_handler_wf_and_dsl: ErrorHandlerWfAndDslT,
    failing_dsl: DSLInput,
    test_worker_factory,
    test_executor_worker_factory,
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
    wf_exec_id = generate_test_exec_id(
        f"{test_workflow_error_handler_success.__name__}_{request.node.callspec.id}"
    )

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
        executor_worker = test_executor_worker_factory(temporal_client)
        _ = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
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
            "TracecatException: Workflow definition not found for wf_0000000000000000000000, version=None",
            id="id-no-match",
        ),
        pytest.param(
            "invalid_error_handler",
            "WorkflowAliasResolutionError: Couldn't find matching workflow for alias 'invalid_error_handler'",
            id="alias-no-match",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.anyio
async def test_workflow_error_handler_invalid_handler_fail_no_match(
    request: pytest.FixtureRequest,
    test_role: Role,
    temporal_client: Client,
    failing_dsl: DSLInput,
    id_or_alias: str,
    expected_err_msg: str,
    test_worker_factory,
    test_executor_worker_factory,
):
    """
    Test that the error handler fails with an invalid error handler that has no matching workflow

    1. Create an error handler
    2. Create a failing workflow
    3. Run the failing workflow
    4. Check that the error handler fails
    """
    test_name = f"{test_workflow_error_handler_invalid_handler_fail_no_match.__name__}_{request.node.callspec.id}"

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
        executor_worker = test_executor_worker_factory(temporal_client)
        _ = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    assert str(exc_info.value) == "Workflow execution failed"
    cause0 = exc_info.value.cause
    assert isinstance(cause0, ActivityError)
    cause1 = cause0.cause
    assert isinstance(cause1, ApplicationError)
    assert str(cause1) == expected_err_msg
    if id_or_alias == "invalid_error_handler":
        err = str(cause1)
        assert "Activity task failed" not in err
        assert "timed out" not in err.lower()


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_lookup_table_success(
    test_role: Role,
    temporal_client: Client,
    test_admin_role: Role,
    test_worker_factory,
    test_executor_worker_factory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    assert data["number"] == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_lookup_table_missing_value(
    test_role: Role,
    temporal_client: Client,
    test_admin_role: Role,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    assert data is None


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_insert_table_row_success(
    test_role: Role,
    temporal_client: Client,
    test_admin_role: Role,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    # Verify the result indicates success
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    assert data is not None
    assert isinstance(data, Mapping)
    assert "id" in data
    assert "number" in data
    assert data["number"] == 42

    # Verify the row was actually inserted
    async with TablesService.with_session(role=test_admin_role) as service:
        rows = await service.lookup_rows(table_name, columns=["number"], values=[42])
    assert len(rows) == 1
    assert rows[0]["number"] == 42


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_table_actions_in_loop(
    test_role: Role,
    temporal_client: Client,
    test_admin_role: Role,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    # Verify the results
    assert result is not None
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    assert "inserted_rows" in data
    assert "looked_up_rows" in data

    # Check inserted rows
    inserted_rows = data["inserted_rows"]
    assert len(inserted_rows) == 5
    for i, row in enumerate(inserted_rows, 1):
        assert row["number"] == i
        assert row["squared"] == i * i

    # Check looked up rows
    looked_up_rows = data["looked_up_rows"]
    assert len(looked_up_rows) == 5
    for i, row in enumerate(looked_up_rows, 1):
        assert row["number"] == i
        assert row["squared"] == i * i

    # Verify the rows were actually inserted in the database
    async with TablesService.with_session(role=test_admin_role) as service:
        table = await service.get_table_by_name(table_name)
        rows = (
            await service.list_rows(
                table,
                CursorPaginationParams(limit=100, cursor=None, reverse=False),
                order_by="created_at",
                sort="asc",
            )
        ).items

    assert len(rows) == 5
    for i, row in enumerate(sorted(rows, key=lambda r: r["number"]), 1):
        assert row["number"] == i
        assert row["squared"] == i * i


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_detached_child_workflow(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
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
    executor_worker = test_executor_worker_factory(temporal_client)
    async with worker, executor_worker:
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

        # Materialize StoredObject results for value assertions.
        results = [await to_data(r) for r in tg.results()]
        assert results == [1001, 1002, 1003]


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_with_child_workflow(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
    """Test that scatter works with child workflow execution.

    This test verifies that:
    1. A scatter action can be followed by a child workflow execution in the scatter region
    2. Each item in the scattered collection triggers a separate child workflow
    3. The gather action properly collects results from all child workflows
    """
    test_name = f"{test_scatter_with_child_workflow.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Create a simple child workflow that doubles the input
    child_dsl = DSLInput(
        title="Child doubler",
        description="Child workflow that doubles the input number",
        entrypoint=DSLEntrypoint(ref="double"),
        actions=[
            ActionStatement(
                ref="double",
                action="core.transform.reshape",
                args={"value": "${{ TRIGGER.number * 2 }}"},
            )
        ],
        returns="${{ ACTIONS.double.result }}",
    )

    _ = await _create_and_commit_workflow(child_dsl, test_role, alias="child_doubler")

    # Create parent workflow with scatter -> child workflow -> gather
    parent_dsl = DSLInput(
        title="Scatter with child workflow",
        description="Test scatter followed by child workflow execution",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(collection="${{ [1, 2, 3, 4] }}").model_dump(),
            ),
            ActionStatement(
                ref="call_child",
                action="core.workflow.execute",
                depends_on=["scatter"],
                args={
                    "workflow_alias": "child_doubler",
                    "trigger_inputs": {"number": "${{ ACTIONS.scatter.result }}"},
                    "wait_strategy": WaitStrategy.WAIT.value,
                },
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["call_child"],
                args=GatherArgs(items="${{ ACTIONS.call_child.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(
        dsl=parent_dsl,
        role=test_role,
        wf_id=TEST_WF_ID,
    )

    worker = test_worker_factory(temporal_client)
    executor_worker = test_executor_worker_factory(temporal_client)
    result = await _run_workflow(wf_exec_id, run_args, worker, executor_worker)

    # Each item should be doubled: [1, 2, 3, 4] -> [2, 4, 6, 8]
    expected = ExecutionContext(
        ACTIONS={
            "gather": TaskResult(
                result=InlineObject(data=[2, 4, 6, 8]),
                result_typename="list",
            )
        },
        TRIGGER=None,
    )
    await assert_context_equal(result, expected)


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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {"result": [2, 3, 4], "result_typename": "list"}
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "a": {"result": [1, 2, 3], "result_typename": "list"},
                        "gather": {"result": [2, 3, 4], "result_typename": "list"},
                        "c": {"result": [2, 3, 4], "result_typename": "list"},
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather2": {
                            "result": [[2, 3], [4, 5]],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        # First block: [10, 20] -> [11, 21]
                        # Second block: [11, 21] -> [111, 121]
                        "gather1": {"result": [11, 21], "result_typename": "list"},
                        "gather2": {"result": [111, 121], "result_typename": "list"},
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        # ex1: [1,2] -> A: [2,4] -> im1: [2,4]
                        # ex2: [10,20] -> B: [15,25] -> im2: [15,25]
                        # C: [2,4,15,25]
                        "im1": {"result": [2, 4], "result_typename": "list"},
                        "im2": {"result": [15, 25], "result_typename": "list"},
                        "c": {"result": [2, 4, 15, 25], "result_typename": "list"},
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {"result": [5, 6, 7], "result_typename": "list"}
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        # Only even numbers: 2, 4, 6 -> 20, 40, 60
                        # Unset values are automatically removed
                        "gather": {
                            "result": [20, 40, 60],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        # Only even numbers: 2, 4, 6 -> 20, 40, 60
                        # Everything else is dropped
                        "gather": {
                            "result": [20, 40, 60],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        # Skip all nulls
                        "gather": {
                            "result": [1, 2, 3, 4, 5, 6],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "a": {
                            "result": 42,
                            "result_typename": "int",
                        }
                        # No other actions should have run
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {
                            "result": [],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather_a": {"result": [10, 20, 30], "result_typename": "list"},
                        "gather_b": {"result": [105, 205], "result_typename": "list"},
                        "join": {
                            "result": [10, 20, 30, 105, 205],
                            "result_typename": "list",
                        },
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {
                            "result": [1, "hello", {"key": "value"}, None, [1, 2]],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "outer_gather": {
                            "result": [[1, 2], [[3, 4], [5, 6]], 7, []],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather_nonempty": {
                            "result": [2, 4, 6],
                            "result_typename": "list",
                        },
                        "gather_empty": {"result": [], "result_typename": "list"},
                        "final_merge": {"result": [2, 4, 6], "result_typename": "list"},
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
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
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "outer_gather": {
                            "result": [[11, 12], [], [13]],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {
                            "result": [],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
            id="scatter-empty-collection",
        ),
        # None collection scatter (treated as empty)
        pytest.param(
            DSLInput(
                title="Scatter None collection",
                description=(
                    "Test that when a scatter's collection expression evaluates to None, "
                    "it is treated as an empty collection. The scatter should not produce "
                    "any execution streams, and the dependent gather action should return "
                    "an empty list."
                ),
                entrypoint=DSLEntrypoint(ref="scatter"),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args={"collection": "${{ None }}"},
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {
                            "result": [],
                            "result_typename": "list",
                        }
                    },
                    "TRIGGER": {},
                }
            ),
            id="scatter-none-collection",
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather": {"result": [], "result_typename": "list"},
                        "reshape2": {"result": [], "result_typename": "list"},
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
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
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather1": {
                            "result": [[1, 2], [3]],
                            "result_typename": "list",
                        },
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather1": {
                            "result": [1, 2],
                            "result_typename": "list",
                        },
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather1": {
                            "result": [],
                            "result_typename": "list",
                            "error": [
                                {
                                    "ref": "throw",
                                    "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 77",
                                    "type": "ExecutionError",
                                    "expr_context": "ACTIONS",
                                    "attempt": 1,
                                    "stream_id": "<root>:0/scatter1:0",
                                    "children": None,
                                },
                                {
                                    "ref": "throw",
                                    "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 77",
                                    "type": "ExecutionError",
                                    "expr_context": "ACTIONS",
                                    "attempt": 1,
                                    "stream_id": "<root>:0/scatter1:1",
                                    "children": None,
                                },
                            ],
                            "error_typename": "list",
                        },
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather1": {
                            "result": [],
                            "result_typename": "list",
                        },
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "gather1": {
                            "result": [
                                {
                                    "ref": "throw",
                                    "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 77",
                                    "type": "ExecutionError",
                                    "expr_context": "ACTIONS",
                                    "attempt": 1,
                                    "stream_id": "<root>:0/scatter1:0",
                                    "children": None,
                                },
                                {
                                    "ref": "throw",
                                    "message": "There was an error in the executor when calling action 'core.transform.reshape'.\n\n\nTracecatExpressionError: Error evaluating expression `1/0`\n\n[evaluator] Evaluation failed at node:\n```\ndiv_op\n  literal\t1\n  literal\t0\n\n```\nReason: Error trying to process rule \"div_op\":\n\nCannot divide by zero\n\n\n------------------------------\nFile: /app/tracecat/expressions/core.py\nFunction: result\nLine: 77",
                                    "type": "ExecutionError",
                                    "expr_context": "ACTIONS",
                                    "attempt": 1,
                                    "stream_id": "<root>:0/scatter1:1",
                                    "children": None,
                                },
                            ],
                            "result_typename": "list",
                        },
                    },
                    "TRIGGER": {},
                }
            ),
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
            dict_to_exec_context(
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
                    "TRIGGER": {},
                }
            ),
            id="scope-shadowing-stream-lookup",
        ),
        pytest.param(
            DSLInput(
                title="run_if can access parent stream context inside scatter",
                description=(
                    "Verify that run_if evaluation inside a scatter branch can read action results "
                    "from an ancestor (parent) stream via stream-aware context resolution."
                ),
                entrypoint=DSLEntrypoint(ref="outside"),
                actions=[
                    # Parent action produces a constant
                    ActionStatement(
                        ref="outside",
                        action="core.transform.reshape",
                        args={"value": "__OUTSIDE__"},
                    ),
                    # Scatter over a simple collection
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        depends_on=["outside"],
                        args=ScatterArgs(collection=[1, 2]).model_dump(),
                    ),
                    # Inside scatter: run_if should consult parent action "a"
                    ActionStatement(
                        ref="inside",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        run_if="${{ ACTIONS.outside.result == '__OUTSIDE__' }}",
                        args={"value": "${{ ACTIONS.scatter.result * 10 }}"},
                    ),
                    # Gather the results from inside
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["inside"],
                        args=GatherArgs(
                            items="${{ ACTIONS.inside.result }}"
                        ).model_dump(),
                    ),
                ],
            ),
            dict_to_exec_context(
                {
                    "ACTIONS": {
                        "outside": {"result": "__OUTSIDE__", "result_typename": "str"},
                        "gather": {"result": [10, 20], "result_typename": "list"},
                    },
                    "TRIGGER": {},
                }
            ),
            id="run-if-stream-aware-context",
        ),
    ],
)
async def test_workflow_scatter_gather(
    request: pytest.FixtureRequest,
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    dsl: DSLInput,
    expected: ExecutionContext,
) -> None:
    """
    Test that a workflow can scatter a collection.
    """
    test_name = f"{test_workflow_scatter_gather.__name__}_{request.node.callspec.id}"
    wf_exec_id = generate_test_exec_id(test_name)
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

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
        # Get result as ExecutionContext and normalize its data
        result_ctx = await to_inline_exec_context(result)
        gather_refs = [
            action.ref
            for action in dsl.actions
            if action.action == "core.transform.gather"
            and action.ref in result_ctx.data["ACTIONS"]
        ]
        for gather_ref in gather_refs:
            assert isinstance(
                result_ctx.data["ACTIONS"][gather_ref].result,
                CollectionObject,
            )
        resolved_result = await resolve_execution_context(result_ctx.data)
        # Normalize the resolved result's data
        normalized_resolved_result = ExecutionContext(
            ACTIONS={
                ref: TaskResult(
                    result=InlineObject(
                        data=normalize_error_line_numbers(
                            await to_data(task_result.result)
                        )
                    ),
                    result_typename=task_result.result_typename,
                    error=normalize_error_line_numbers(task_result.error),
                    error_typename=task_result.error_typename,
                    interaction=task_result.interaction,
                    interaction_id=task_result.interaction_id,
                    interaction_type=task_result.interaction_type,
                    collection_index=task_result.collection_index,
                )
                for ref, task_result in resolved_result["ACTIONS"].items()
            },
            TRIGGER=InlineObject(
                data=normalize_error_line_numbers(
                    await to_data(resolved_result["TRIGGER"])
                )
            )
            if resolved_result["TRIGGER"]
            else None,
        )
        resolved_expected = await resolve_execution_context(expected)
        assert normalized_resolved_result == resolved_expected


@pytest.mark.anyio
async def test_workflow_gather_error_strategy_raise(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
    """Gather should fail-fast when configured with the raise error strategy."""

    dsl = DSLInput(
        title="Gather raise on errors",
        description="Gather configured to raise when any scatter branch errors",
        entrypoint=DSLEntrypoint(ref="scatter1"),
        actions=[
            ActionStatement(
                ref="scatter1",
                action="core.transform.scatter",
                args=ScatterArgs(collection=[1, 2]).model_dump(),
            ),
            ActionStatement(
                ref="throw",
                action="core.transform.reshape",
                depends_on=["scatter1"],
                run_if="${{ FN.mod(ACTIONS.scatter1.result, 2) == 1 }}",
                args={"value": "${{ 1/0 }}"},
            ),
            ActionStatement(
                ref="gather1",
                action="core.transform.gather",
                depends_on=["throw"],
                args=GatherArgs(
                    items="${{ ACTIONS.throw.result }}",
                    error_strategy=StreamErrorHandlingStrategy.RAISE,
                ).model_dump(),
            ),
        ],
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    wf_exec_id = generate_test_exec_id(
        test_workflow_gather_error_strategy_raise.__name__
    )

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
    assert "Gather 'gather1' encountered" in str(cause)
    assert cause.details, "ApplicationError should include gather error details"

    # The details[0] is a dict mapping gather_ref to ActionErrorInfo
    detail = cause.details[0]
    assert isinstance(detail, Mapping)
    assert "gather1" in detail, "Details should contain gather1 error"

    # Validate the gather error structure (stream-aware)
    gather_error = detail["gather1"]
    validated_error = ActionErrorInfoAdapter.validate_python(gather_error)
    assert validated_error.ref == "gather1", "Gather error ref should be gather1"
    assert validated_error.stream_id == "<root>:0", (
        "Gather error should have parent stream_id"
    )
    assert validated_error.children is not None, "Gather error should have children"

    # Validate the child errors from scatter branches
    children = validated_error.children
    assert len(children) == 1, "Should have 1 error from scatter branch"
    child_error = children[0]
    assert child_error.ref == "throw", "Child error should be from 'throw' action"


@pytest.mark.anyio
async def test_workflow_env_and_trigger_access_in_stream(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
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

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Run the workflow and check the result
    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        run_args = DSLRunArgs(
            dsl=dsl,
            role=test_role,
            wf_id=TEST_WF_ID,
            trigger_inputs=InlineObject(data=trigger_data),
        )
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        expected_ctx = ExecutionContext(
            ACTIONS={
                "gather": TaskResult(
                    result=InlineObject(
                        data=[
                            {"exec_id": wf_exec_id, "trigger": trigger_data},
                            {"exec_id": wf_exec_id, "trigger": trigger_data},
                            {"exec_id": wf_exec_id, "trigger": trigger_data},
                        ]
                    ),
                    result_typename="list",
                )
            },
            TRIGGER=InlineObject(data=trigger_data),
        )
        await assert_context_equal(result, expected_ctx)


def assert_result_is_run_context(result: dict[str, Any]) -> bool:
    assert isinstance(result, dict), "Result is not a dict"
    assert result.get("wf_id") == str(TEST_WF_ID), "wf_id is not correct"
    # Check wf_exec_id has the expected format (prefix varies with parametrized test IDs)
    wf_exec_id = result.get("wf_exec_id")
    expected_prefix = f"{TEST_WF_ID.short()}/exec_test_workflow_return_strategy"
    assert isinstance(wf_exec_id, str) and wf_exec_id.startswith(expected_prefix), (
        f"wf_exec_id should start with {expected_prefix}, got {wf_exec_id}"
    )
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
            lambda result: (
                ExecutionContextTA.validate_python(result)
                == ExecutionContext(
                    ACTIONS={
                        "a": TaskResult(
                            result=InlineObject(data=42),
                            result_typename="int",
                        )
                    },
                    TRIGGER=None,
                )
            ),
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
    request: pytest.FixtureRequest,
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    return_strategy: Literal["context", "minimal"],
    validator: Callable[[dict[str, Any]], bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Test that the workflow return strategy is respected.
    """
    monkeypatch.setenv("TRACECAT__WORKFLOW_RETURN_STRATEGY", return_strategy)
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_RETURN_STRATEGY", return_strategy)
    test_name = f"{test_workflow_return_strategy.__name__}_{request.node.callspec.id}"
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

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
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
        # Unwrap StoredObject to compare actual data (handles both inline and external)
        data = await to_data(result)
        # For context strategy, resolve nested StoredObjects in ExecutionContext
        if return_strategy == "context":
            data = await resolve_exec_ctx_from_dict(data)
        assert validator(data)


@pytest.mark.anyio
async def test_workflow_environment_override(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Test that the correct secrets are used for the action environment override.
    """
    # Set return strategy to context so we can verify the result
    monkeypatch.setenv("TRACECAT__WORKFLOW_RETURN_STRATEGY", "context")
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_RETURN_STRATEGY", "context")
    # Disable secret masking so we can verify the actual secret value
    monkeypatch.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    test_name = f"{test_workflow_environment_override.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Set up secrets in different environments
    async with SecretsService.with_session(role=test_role) as secrets_service:
        # Create secret in default environment
        await secrets_service.create_secret(
            SecretCreate(
                name=f"{test_name}_secret",
                environment="default",
                keys=[
                    SecretKeyValue(
                        key="API_KEY", value=SecretStr(f"{test_name}_default_value")
                    )
                ],
            ),
        )

        # Create secret in override environment
        await secrets_service.create_secret(
            SecretCreate(
                name="test_secret",
                environment="override_env",
                keys=[
                    SecretKeyValue(
                        key="API_KEY", value=SecretStr(f"{test_name}_override_value")
                    )
                ],
            )
        )

        # Define the DSL workflow
        dsl = DSLInput(
            title="Workflow action environment override",
            description="Test that the workflow action environment override is respected",
            entrypoint=DSLEntrypoint(ref="a"),
            config=DSLConfig(environment="default"),  # Workflow default environment
            actions=[
                ActionStatement(
                    ref="a",
                    action="core.transform.reshape",
                    args={"value": "${{ SECRETS.test_secret.API_KEY }}"},
                    environment="override_env",  # Action environment override
                ),
            ],
        )

        async with (
            test_worker_factory(temporal_client),
            test_executor_worker_factory(temporal_client),
        ):
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
            # Verify that the action used the secret from the override environment
            expected = ExecutionContext(
                ACTIONS={
                    "a": TaskResult(
                        result=InlineObject(data=f"{test_name}_override_value"),
                        result_typename="str",
                    )
                },
                TRIGGER=None,
            )
            await assert_context_equal(result, expected)


@pytest.mark.anyio
async def test_workflow_trigger_defaults(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
    """
    Test that TRIGGER defaults are applied when not provided in DSLRunArgs.
    """
    # Prepare test TRIGGER data and expected execution_id
    trigger_data = {}
    test_name = f"{test_workflow_trigger_defaults.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Workflow trigger defaults",
        description="Test that TRIGGER defaults are applied when not provided",
        entrypoint=DSLEntrypoint(
            ref="a",
            expects={
                "default_field": ExpectedField(type="str", default="default_value"),
                "default_number": ExpectedField(type="int", default=42),
            },
        ),
        actions=[
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                args={"value": "${{ TRIGGER }}"},
            ),
        ],
        returns="${{ ACTIONS.a.result }}",
    )

    # Prepare expected result with defaults applied
    expected = {
        "default_field": "default_value",
        "default_number": 42,
    }

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Run the workflow and check the result
    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        run_args = DSLRunArgs(
            dsl=dsl,
            role=test_role,
            wf_id=TEST_WF_ID,
            trigger_inputs=InlineObject(data=trigger_data),
        )
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        # Unwrap StoredObject to compare actual data (handles both inline and external)
        data = await to_data(result)
        assert data == expected


@pytest.mark.anyio
async def test_workflow_trigger_validation_error_details(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
) -> None:
    """Ensure trigger validation errors surface field-level details to callers."""

    invalid_trigger_data = {"default_number": "not-an-int"}
    test_name = f"{test_workflow_trigger_validation_error_details.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Workflow trigger validation",
        description="Test that trigger validation errors propagate details",
        entrypoint=DSLEntrypoint(
            ref="a",
            expects={
                "default_field": ExpectedField(type="str", default="default_value"),
                "default_number": ExpectedField(type="int", default=42),
            },
        ),
        actions=[
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                args={"value": "${{ TRIGGER }}"},
            ),
        ],
        returns="${{ ACTIONS.a.result }}",
    )

    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        run_args = DSLRunArgs(
            dsl=dsl,
            role=test_role,
            wf_id=TEST_WF_ID,
            trigger_inputs=InlineObject(data=invalid_trigger_data),
        )
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
    assert "Failed to validate trigger inputs" in str(cause)

    details = cause.details
    if isinstance(details, list | tuple):
        detail_messages = [str(d) for d in details]
    elif details is None:
        detail_messages = []
    else:
        detail_messages = [str(details)]

    assert detail_messages, "Trigger validation error should include details"
    combined_details = "\n".join(detail_messages)
    assert "default_number" in combined_details
    assert "valid integer" in combined_details or '"type": "int' in combined_details


@pytest.mark.anyio
async def test_workflow_time_anchor_deterministic_time_functions(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    """Test that FN.now()/utcnow()/today() return logical time = time_anchor + elapsed.

    This test verifies:
    1. Time functions return values based on time_anchor (not wall clock)
    2. Time advances between sequential actions (logical time = time_anchor + elapsed)
    3. FN.wall_clock() ignores time_anchor and returns actual current time
    """
    # Use a specific time_anchor that's clearly in the past
    time_anchor = datetime(2024, 6, 15, 14, 30, 45, tzinfo=UTC)

    # Create a workflow with sequential actions to verify time advances
    dsl = DSLInput(
        **{
            "title": "Time Anchor Test",
            "description": "Test deterministic time functions with time_anchor",
            "entrypoint": {"expects": {}, "ref": "action_1"},
            "actions": [
                {
                    "ref": "action_1",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "utcnow_iso": "${{ FN.to_isoformat(FN.utcnow()) }}",
                            "now_iso": "${{ FN.to_isoformat(FN.now()) }}",
                            "today_str": "${{ str(FN.today()) }}",
                            "wall_clock_iso": "${{ FN.to_isoformat(FN.wall_clock()) }}",
                        }
                    },
                },
                {
                    "ref": "action_2",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "utcnow_iso": "${{ FN.to_isoformat(FN.utcnow()) }}",
                        }
                    },
                    "depends_on": ["action_1"],
                },
                {
                    "ref": "action_3",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "utcnow_iso": "${{ FN.to_isoformat(FN.utcnow()) }}",
                        }
                    },
                    "depends_on": ["action_2"],
                },
                {
                    "ref": "combine",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "action_1": "${{ ACTIONS.action_1.result }}",
                            "action_2": "${{ ACTIONS.action_2.result }}",
                            "action_3": "${{ ACTIONS.action_3.result }}",
                        }
                    },
                    "depends_on": ["action_3"],
                },
            ],
            "returns": "${{ ACTIONS.combine.result }}",
        }
    )

    test_name = "test_workflow_time_anchor_deterministic"
    wf_exec_id = generate_test_exec_id(test_name)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=test_role,
                wf_id=TEST_WF_ID,
                time_anchor=time_anchor,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            run_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    # Extract times from each action
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    action_1 = data["action_1"]
    action_2 = data["action_2"]
    action_3 = data["action_3"]

    time_1 = datetime.fromisoformat(action_1["utcnow_iso"])
    time_2 = datetime.fromisoformat(action_2["utcnow_iso"])
    time_3 = datetime.fromisoformat(action_3["utcnow_iso"])

    # Verify all times are based on time_anchor date and do not move backwards
    assert time_1.date() == time_anchor.date()
    assert time_2.date() == time_anchor.date()
    assert time_3.date() == time_anchor.date()
    assert time_1 >= time_anchor, (
        f"action_1 time {time_1} should be >= time_anchor {time_anchor}"
    )

    # Verify time advances between sequential actions
    # (logical time = time_anchor + elapsed workflow time)
    assert time_2 >= time_1, (
        f"action_2 time {time_2} should be >= action_1 time {time_1}"
    )
    assert time_3 >= time_2, (
        f"action_3 time {time_3} should be >= action_2 time {time_2}"
    )

    # Verify FN.today() used the time_anchor date
    assert action_1["today_str"].startswith(
        "2024-06-1"
    )  # Could be 14 or 15 depending on TZ

    # Verify FN.now() used the time_anchor (converted to local timezone)
    assert "2024-06-1" in action_1["now_iso"]

    # Verify FN.wall_clock() did NOT use time_anchor
    wall_clock_year = int(action_1["wall_clock_iso"][:4])
    assert wall_clock_year >= 2024
    assert "2024-06-15T14:30:45" not in action_1["wall_clock_iso"]


@pytest.mark.anyio
async def test_workflow_time_anchor_inherited_by_child_workflow(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: WorkerFactory,
    test_executor_worker_factory: WorkerFactory,
):
    """Test that child workflows continue logical time from parent's current position.

    This test verifies:
    1. Child workflows receive parent's current logical time (not raw time_anchor)
    2. Child's FN.utcnow() returns time >= parent's, since child starts after parent
    3. Both parent and child times are based on the same original time_anchor
    4. Time progresses continuously across parent -> child workflow boundary
    """
    # Use a specific time_anchor that's clearly in the past
    time_anchor = datetime(2024, 6, 15, 14, 30, 45, tzinfo=UTC)

    # Create a child workflow that returns the current time
    child_dsl = DSLInput(
        title="Time Anchor Child",
        description="Child workflow that returns anchored time",
        entrypoint=DSLEntrypoint(expects={}, ref="get_time"),
        actions=[
            ActionStatement(
                ref="get_time",
                action="core.transform.reshape",
                args={
                    "value": {
                        "utcnow_iso": "${{ FN.to_isoformat(FN.utcnow()) }}",
                        "today_str": "${{ str(FN.today()) }}",
                    }
                },
            ),
        ],
        returns="${{ ACTIONS.get_time.result }}",
    )

    child_workflow = await _create_and_commit_workflow(child_dsl, test_role)

    # Create a parent workflow that calls the child and also captures its own time
    parent_dsl = DSLInput(
        title="Time Anchor Parent",
        description="Parent workflow that calls child and compares times",
        entrypoint=DSLEntrypoint(ref="parent_time"),
        actions=[
            ActionStatement(
                ref="parent_time",
                action="core.transform.reshape",
                args={
                    "value": {
                        "utcnow_iso": "${{ FN.to_isoformat(FN.utcnow()) }}",
                    }
                },
            ),
            ActionStatement(
                ref="call_child",
                action="core.workflow.execute",
                args={
                    "workflow_id": child_workflow.id,
                    "trigger_inputs": {},
                },
                depends_on=["parent_time"],
            ),
            ActionStatement(
                ref="combine_results",
                action="core.transform.reshape",
                args={
                    "value": {
                        "parent_utcnow": "${{ ACTIONS.parent_time.result.utcnow_iso }}",
                        "child_utcnow": "${{ ACTIONS.call_child.result.utcnow_iso }}",
                        "child_today": "${{ ACTIONS.call_child.result.today_str }}",
                    }
                },
                depends_on=["call_child"],
            ),
        ],
        returns="${{ ACTIONS.combine_results.result }}",
    )

    test_name = "test_workflow_time_anchor_inherited_by_child"
    wf_exec_id = generate_test_exec_id(test_name)

    async with (
        test_worker_factory(temporal_client),
        test_executor_worker_factory(temporal_client),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=parent_dsl,
                role=test_role,
                wf_id=TEST_WF_ID,
                time_anchor=time_anchor,
            ),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            run_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    # Both parent and child times should be based on the same time_anchor.
    # Unwrap StoredObject to compare actual data (handles both inline and external)
    data = await to_data(result)
    parent_time = datetime.fromisoformat(data["parent_utcnow"])
    assert parent_time.date() == time_anchor.date()
    assert parent_time >= time_anchor, (
        f"Parent time {parent_time} should be >= time_anchor {time_anchor}"
    )

    # Child time should be >= parent time since child continues from parent's position
    # (child starts after some workflow time has elapsed from when parent evaluated its time)
    child_time = datetime.fromisoformat(data["child_utcnow"])
    assert parent_time >= time_anchor
    assert child_time >= parent_time, (
        f"Child time {child_time} should be >= parent time {parent_time}"
    )
    # Child should be based on the same date as parent (from the time_anchor)
    assert child_time.date() == parent_time.date()

    # Child's today should also be based on the time_anchor
    assert data["child_today"].startswith("2024-06-1")
