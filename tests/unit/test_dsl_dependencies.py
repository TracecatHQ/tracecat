from asyncio import CancelledError, to_thread
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.dsl._converter import PydanticORJSONPayloadConverter
from tracecat.dsl.action import (
    DSLActivities,
    EvaluateTemplatedObjectActivityInput,
    materialize_context,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.compiler import compile_dsl_dependencies
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    DSLDependencyPlan,
    ExecutionContext,
    RunContext,
    StreamID,
    TaskResult,
)
from tracecat.dsl.types import Task
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.dsl.workflow_logging import get_workflow_logger
from tracecat.expressions.eval import eval_templated_object
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import RuntimeErrorKind, RuntimeErrorOwner
from tracecat.storage.object import ExternalObject, InlineObject, ObjectRef
from tracecat.temporal.errors import extract_error_classification
from tracecat.temporal.patches import WorkflowPatch


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def context() -> ExecutionContext:
    return ExecutionContext(
        ACTIONS={
            "first": TaskResult.from_result({"value": 3}),
            "second": TaskResult.from_result({"value": 7}),
            "unused": TaskResult.from_result({"value": 11}),
        },
        TRIGGER=InlineObject(data={"value": 5}),
        VARS={"label": "summary"},
    )


def make_dsl(returns: Any) -> DSLInput:
    return DSLInput(
        title="Dependency compilation",
        entrypoint=DSLEntrypoint(ref="first"),
        description="Synthetic expression dependencies",
        actions=[
            ActionStatement(ref=ref, action="core.noop")
            for ref in ("first", "second", "unused")
        ],
        returns=returns,
    )


@pytest.mark.parametrize(
    ("returns", "expected_refs"),
    [
        ("${{ ACTIONS.first.result.value }}", ["first"]),
        ("${{ ACTIONS.first }}", ["first"]),
        ("${{ ACTIONS.first.result['value'] }}", ["first"]),
        ("${{ ACTIONS.first.result[*].value }}", ["first"]),
        ("${{ ACTIONS.second.result + ACTIONS.first.result }}", ["first", "second"]),
        ({"items": ["${{ ACTIONS.first.result }}", "${{ TRIGGER.value }}"]}, ["first"]),
        ({"${{ ACTIONS.first.result }}": "literal"}, ["first"]),
        ("${{ VARS.label }}: ${{ ACTIONS.second.result }}", ["second"]),
        (
            "${{ ACTIONS.first.result if ACTIONS.second.result else ACTIONS.first.result }}",
            ["first", "second"],
        ),
        ("${{ TRIGGER }}", []),
        ({"constant": [1, True, None]}, []),
        ("ACTIONS.unused.result", []),
        (False, []),
        (None, []),
    ],
)
def test_compile_return_dependencies(returns: Any, expected_refs: list[str]) -> None:
    assert compile_dsl_dependencies(make_dsl(returns)).returns == expected_refs


@pytest.mark.parametrize(
    "returns",
    [
        "${{ ACTIONS.*.result }}",
        "${{ ACTIONS..value }}",
        "${{ ACTIONS['first'].result }}",
        "${{ ACTIONS.first.`parent`.second.result }}",
        "${{ ACTIONS.first.result.`parent`.`parent`.second.result }}",
        "${{ ACTIONS.missing.result }}",
        "${{ ACTIONS.first.result + }}",
    ],
)
@pytest.mark.anyio
async def test_compile_activity_falls_back_for_unsupported_references(
    returns: str,
) -> None:
    assert (
        await DSLActivities.compile_dsl_dependencies_activity(make_dsl(returns)) is None
    )


@pytest.mark.anyio
async def test_compile_activity_fails_open_on_unexpected_compiler_error() -> None:
    with patch(
        "tracecat.dsl.action.compile_dsl_dependencies",
        side_effect=RuntimeError("synthetic compiler failure"),
    ):
        plan = await DSLActivities.compile_dsl_dependencies_activity(make_dsl(None))
    assert plan is None


@pytest.mark.anyio
async def test_compile_activity_preserves_cancellation() -> None:
    with (
        patch(
            "tracecat.dsl.action.compile_dsl_dependencies", side_effect=CancelledError
        ),
        pytest.raises(CancelledError),
    ):
        await DSLActivities.compile_dsl_dependencies_activity(make_dsl(None))


@pytest.mark.anyio
async def test_compiler_fallback_does_not_suppress_invalid_return(
    context: ExecutionContext,
) -> None:
    expression = "${{ ACTIONS.first.result + }}"
    assert (
        await DSLActivities.compile_dsl_dependencies_activity(make_dsl(expression))
        is None
    )
    with pytest.raises(ApplicationError) as exc:
        await to_thread(
            DSLActivities.resolve_return_expression_activity,
            EvaluateTemplatedObjectActivityInput(
                obj=expression, operand=context, key="test/invalid-return"
            ),
        )
    classification = extract_error_classification(exc.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_EXPRESSION_INVALID
    assert exc.value.non_retryable


def test_dependencies_are_per_consumer() -> None:
    dsl = DSLInput(
        title="Direct dependencies",
        entrypoint=DSLEntrypoint(ref="fetch"),
        description="Keep transitive dependencies out of activity inputs",
        actions=[
            ActionStatement(ref="fetch", action="core.noop"),
            ActionStatement(
                ref="transform",
                action="core.noop",
                depends_on=["fetch"],
                args={"input": "${{ ACTIONS.fetch.result }}"},
            ),
            ActionStatement(
                ref="summarize",
                action="core.noop",
                depends_on=["transform"],
                args={"input": "${{ ACTIONS.transform.result }}"},
            ),
        ],
        returns="${{ ACTIONS.summarize.result }}",
    )
    plan = compile_dsl_dependencies(dsl)
    assert plan.actions == {
        "fetch": [],
        "transform": ["fetch"],
        "summarize": ["transform"],
    }
    assert plan.returns == ["summarize"]


def test_action_dependencies_cover_control_expressions() -> None:
    dsl = make_dsl(None)
    dsl.actions[-1] = ActionStatement(
        ref="unused",
        action="core.noop",
        args={"input": "${{ ACTIONS.first.result }}"},
        run_if="${{ ACTIONS.second.result }}",
        for_each="${{ ACTIONS.first.result }}",
        environment="${{ ACTIONS.second.result }}",
    )
    assert compile_dsl_dependencies(dsl).actions["unused"] == ["first", "second"]


@pytest.mark.anyio
async def test_handle_return_does_not_send_unreferenced_inline_results() -> None:
    workflow = object.__new__(DSLWorkflow)
    workflow.dsl = DSLInput(
        title="Return payload regression",
        description="Synthetic accumulated inline results",
        entrypoint=DSLEntrypoint(ref="summary"),
        actions=[ActionStatement(ref="summary", action="core.transform.reshape")],
        returns="${{ ACTIONS.summary.result }}",
    )
    workflow.dependency_plan = await DSLActivities.compile_dsl_dependencies_activity(
        workflow.dsl
    )
    workflow.context = ExecutionContext(
        ACTIONS={
            f"step_{index}": TaskResult.from_result("x" * (100 * 1024))
            for index in range(24)
        },
        TRIGGER=None,
    )
    workflow.context["ACTIONS"]["summary"] = TaskResult.from_result({"count": 24})
    workflow.workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    workflow.wf_exec_id = "test-return-execution"
    workflow.start_to_close_timeout = timedelta(seconds=60)
    workflow.logger = get_workflow_logger()
    stored = InlineObject(data={"count": 24})
    execute = AsyncMock(return_value=stored)
    converter = PydanticORJSONPayloadConverter()
    unfiltered = EvaluateTemplatedObjectActivityInput(
        obj=workflow.dsl.returns, operand=workflow.context, key="test/return"
    )
    oversized = converter.to_payload(unfiltered)
    assert oversized is not None and oversized.ByteSize() > 2 * 1024 * 1024

    with (
        patch.object(workflow, "_set_logical_time_context"),
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute),
    ):
        assert await workflow._handle_return() is stored

    execute.assert_awaited_once()
    assert execute.call_args.args == (DSLActivities.resolve_return_expression_activity,)
    activity_input = execute.call_args.kwargs["arg"]
    assert isinstance(activity_input, EvaluateTemplatedObjectActivityInput)
    payload = converter.to_payload(activity_input)
    assert payload is not None and payload.ByteSize() < 1024
    assert set(activity_input.operand["ACTIONS"]) == {"summary"}
    assert len(workflow.context["ACTIONS"]) == 25
    assert eval_templated_object(
        activity_input.obj, operand=await materialize_context(activity_input.operand)
    ) == {"count": 24}


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failed", [True, False])
async def test_compilation_is_patch_gated(enabled: bool, failed: bool) -> None:
    workflow = object.__new__(DSLWorkflow)
    workflow.dsl = make_dsl("${{ ACTIONS.first.result }}")
    workflow.start_to_close_timeout = timedelta(seconds=60)
    plan = None if failed else compile_dsl_dependencies(workflow.dsl)
    execute = AsyncMock(return_value=plan)
    with (
        patch(
            "tracecat.dsl.workflow.workflow.patched", return_value=enabled
        ) as patched,
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute),
    ):
        await workflow._compile_dependencies()
    assert workflow.dependency_compilation_failed is (enabled and failed)
    patched.assert_called_once_with(WorkflowPatch.COMPILE_DSL_DEPENDENCIES)
    if enabled:
        assert workflow.dependency_plan is plan
        execute.assert_awaited_once()
        assert execute.call_args.args == (
            DSLActivities.compile_dsl_dependencies_activity,
        )
        assert execute.call_args.kwargs["arg"] is workflow.dsl
    else:
        assert workflow.dependency_plan is None
        execute.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("compiled", [True, False])
async def test_return_context_uses_plan_and_preserves_stored_objects(
    context: ExecutionContext, compiled: bool
) -> None:
    stored = ExternalObject(
        ref=ObjectRef(
            bucket="test-results",
            key="test/result.json",
            size_bytes=3 * 1024 * 1024,
            sha256="0" * 64,
        ),
        typename="dict",
    )
    context["ACTIONS"]["first"] = TaskResult(result=stored, result_typename="dict")
    workflow = object.__new__(DSLWorkflow)
    workflow.dsl = make_dsl("${{ ACTIONS.first.result }}")
    workflow.dependency_plan = (
        compile_dsl_dependencies(workflow.dsl) if compiled else None
    )
    workflow.context = context
    workflow.workspace_id = UUID(int=1)
    workflow.wf_exec_id = "test-return-execution"
    workflow.start_to_close_timeout = timedelta(seconds=60)
    workflow.logger = get_workflow_logger()
    execute = AsyncMock(return_value=InlineObject(data=None))
    with (
        patch.object(workflow, "_set_logical_time_context"),
        patch("tracecat.dsl.workflow.workflow.execute_activity", new=execute),
        patch(
            "tracecat.expressions.parser.core.parser.parse",
            side_effect=AssertionError("must not parse at return"),
        ),
    ):
        await workflow._handle_return()
    operand = execute.call_args.kwargs["arg"].operand
    assert set(operand["ACTIONS"]) == (
        {"first"} if compiled else set(context["ACTIONS"])
    )
    assert operand["ACTIONS"]["first"].result is stored
    assert operand["TRIGGER"] is context["TRIGGER"]
    assert operand.get("VARS") == context.get("VARS")
    assert len(context["ACTIONS"]) == 3


def test_plan_selects_live_stream_results_without_parsing(
    context: ExecutionContext,
) -> None:
    scheduler = object.__new__(DSLScheduler)
    scheduler.dependency_compilation_failed = False
    scheduler.dependency_plan = DSLDependencyPlan(
        actions={"consumer": ["first", "second", "skipped"]}, returns=[]
    )
    scheduler.logger = get_workflow_logger()
    scheduler._root_context = context
    child = StreamID("child")
    scheduler.streams = {
        ROOT_STREAM: context,
        child: ExecutionContext(
            ACTIONS={"first": TaskResult.from_result("child-value")}, TRIGGER=None
        ),
    }
    scheduler.stream_hierarchy = {ROOT_STREAM: None, child: ROOT_STREAM}
    task = ActionStatement(ref="consumer", action="core.noop")
    with patch(
        "tracecat.dsl.scheduler.extract_expressions",
        side_effect=AssertionError("must use compiled dependencies"),
    ):
        operand = scheduler.build_stream_aware_context(task, child)
        assert set(operand["ACTIONS"]) == {"first", "second"}
        assert operand["ACTIONS"]["first"].result == InlineObject(data="child-value")
        assert operand["ACTIONS"]["second"] is context["ACTIONS"]["second"]
        assert operand["TRIGGER"] is context["TRIGGER"]
        scheduler.streams[child]["ACTIONS"]["first"] = TaskResult.from_result(
            "next-iteration"
        )
        updated = scheduler.build_stream_aware_context(task, child)
        assert updated["ACTIONS"]["first"].result == InlineObject(data="next-iteration")


@pytest.mark.anyio
@pytest.mark.parametrize("nested", [False, True])
async def test_compiler_fallback_preserves_full_action_context(
    context: ExecutionContext, nested: bool
) -> None:
    expression = "${{ ACTIONS.first.`parent`.second.result.value }}"
    dsl = make_dsl(None)
    task = dsl.actions[-1]
    task.args = {"value": expression}
    scheduler = object.__new__(DSLScheduler)
    scheduler.dependency_plan = await DSLActivities.compile_dsl_dependencies_activity(
        dsl
    )
    assert scheduler.dependency_plan is None
    scheduler.dependency_compilation_failed = True
    scheduler._root_context = context
    scheduler.streams = {ROOT_STREAM: context}
    scheduler.stream_hierarchy = {ROOT_STREAM: None}
    stream_id = ROOT_STREAM
    if nested:
        parent, stream_id, sibling = map(StreamID, ("parent", "child", "sibling"))
        scheduler.streams[parent] = ExecutionContext(
            ACTIONS={"second": TaskResult.from_result({"value": 13})}, TRIGGER=None
        )
        scheduler.streams[stream_id] = ExecutionContext(
            ACTIONS={"first": TaskResult.from_result({"value": 17})}, TRIGGER=None
        )
        scheduler.streams[sibling] = ExecutionContext(
            ACTIONS={"sibling_only": TaskResult.from_result(19)}, TRIGGER=None
        )
        scheduler.stream_hierarchy.update(
            {parent: ROOT_STREAM, stream_id: parent, sibling: parent}
        )
    workflow = object.__new__(DSLWorkflow)
    workflow.scheduler = scheduler
    with patch(
        "tracecat.dsl.scheduler.extract_expressions",
        side_effect=AssertionError("fallback must not run the legacy extractor"),
    ):
        operand = workflow._build_action_context(task, stream_id)
        assert scheduler._build_collection_context(task, stream_id) == operand
    assert set(operand["ACTIONS"]) == {"first", "second", "unused"}
    assert operand["ACTIONS"]["unused"] is context["ACTIONS"]["unused"]
    assert operand["TRIGGER"] is context["TRIGGER"]
    assert operand.get("VARS") is context.get("VARS")
    assert eval_templated_object(
        expression, operand=await materialize_context(operand)
    ) == (13 if nested else 7)
    assert operand["ACTIONS"]["first"].result == InlineObject(
        data={"value": 17 if nested else 3}
    )
    assert context["ACTIONS"]["second"].result == InlineObject(data={"value": 7})


def test_legacy_action_context_still_uses_sparse_extraction(
    context: ExecutionContext,
) -> None:
    scheduler = object.__new__(DSLScheduler)
    scheduler.dependency_compilation_failed = False
    scheduler.dependency_plan = None
    scheduler.logger = get_workflow_logger()
    scheduler._root_context = context
    scheduler.streams = {ROOT_STREAM: context}
    scheduler.stream_hierarchy = {ROOT_STREAM: None}
    task = ActionStatement(
        ref="consumer",
        action="core.noop",
        args={"value": "${{ ACTIONS.first.result }}"},
    )
    assert set(scheduler.build_stream_aware_context(task, ROOT_STREAM)["ACTIONS"]) == {
        "first"
    }


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["scatter", "gather"])
@pytest.mark.parametrize("compiled", [True, False])
async def test_collection_activity_inputs_use_consumer_dependencies(
    context: ExecutionContext, kind: str, compiled: bool
) -> None:
    scheduler = object.__new__(DSLScheduler)
    scheduler.dependency_compilation_failed = False
    scheduler.logger = get_workflow_logger()
    scheduler.role = Role(
        type="service", service_id="tracecat-runner", workspace_id=UUID(int=1)
    )
    wf_id = WorkflowUUID.new_uuid4()
    scheduler.run_context = RunContext(
        wf_id=wf_id,
        wf_exec_id=f"{wf_id.short()}/exec_test",
        wf_run_id=uuid4(),
        environment="test",
        logical_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    scheduler._root_context = context
    child = StreamID.new("scatter", 0)
    scheduler.streams = {ROOT_STREAM: context, child: context}
    scheduler.stream_hierarchy = {ROOT_STREAM: None, child: ROOT_STREAM}
    args = {
        "collection" if kind == "scatter" else "items": "${{ ACTIONS.first.result }}"
    }
    stmt = ActionStatement(ref=kind, action=f"core.{kind}", args=args)
    scheduler.dependency_plan = None
    if compiled:
        scheduler.dependency_plan = DSLDependencyPlan(
            actions={kind: ["first"]}, returns=[]
        )
    stream_id = ROOT_STREAM if kind == "scatter" else child
    task = Task(ref=kind, stream_id=stream_id)
    execute = AsyncMock(side_effect=CancelledError("stop after recording the input"))
    with (
        patch("tracecat.dsl.scheduler.workflow.execute_activity", new=execute),
        pytest.raises(CancelledError),
    ):
        if kind == "scatter":
            await scheduler._handle_scatter(task, stmt)
        else:
            await scheduler._handle_gather(task, stmt)
    execute.assert_awaited_once()
    operand = execute.call_args.kwargs["arg"].operand
    assert set(operand["ACTIONS"]) == (
        {"first"} if compiled else set(context["ACTIONS"])
    )
    assert len(context["ACTIONS"]) == 3
