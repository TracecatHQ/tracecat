from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from tracecat.dsl._converter import PydanticORJSONPayloadConverter
from tracecat.dsl.action import (
    DSLActivities,
    EvaluateTemplatedObjectActivityInput,
    materialize_context,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.return_context import build_return_context
from tracecat.dsl.schemas import ActionStatement, ExecutionContext, TaskResult
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.dsl.workflow_logging import get_workflow_logger
from tracecat.expressions.eval import eval_templated_object
from tracecat.storage.object import ExternalObject, InlineObject, ObjectRef


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("returns", "expected_refs"),
    [
        ("${{ ACTIONS.first.result.value }}", {"first"}),
        ("${{ ACTIONS.first }}", {"first"}),
        ("${{ ACTIONS.first.result['value'] }}", {"first"}),
        (
            "${{ ACTIONS.first.result.value + ACTIONS.second.result.value }}",
            {"first", "second"},
        ),
        (
            {"items": ["${{ ACTIONS.first.result }}", "${{ TRIGGER.value }}"]},
            {"first"},
        ),
        (
            "${{ VARS.label }}: ${{ ACTIONS.second.result.value }}",
            {"second"},
        ),
        ("${{ TRIGGER }}", set()),
        ({"constant": [1, True, None]}, set()),
        ("ACTIONS.unused.result", set()),
        (False, set()),
    ],
)
async def test_return_context_preserves_evaluation(
    context: ExecutionContext, returns: Any, expected_refs: set[str]
) -> None:
    operand = build_return_context(returns, context)

    assert set(operand["ACTIONS"]) == expected_refs
    assert set(context["ACTIONS"]) == {"first", "second", "unused"}
    assert operand["TRIGGER"] is context["TRIGGER"]
    assert operand.get("VARS") is context.get("VARS")
    for ref in expected_refs:
        assert operand["ACTIONS"][ref] is context["ACTIONS"][ref]
    assert eval_templated_object(
        returns, operand=await materialize_context(operand)
    ) == eval_templated_object(returns, operand=await materialize_context(context))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "returns",
    [
        "${{ ACTIONS.*.result }}",
        "${{ ACTIONS..value }}",
        "${{ ACTIONS['first'].result }}",
        ["${{ ACTIONS.first.result }}", "${{ ACTIONS.*.result }}"],
    ],
)
async def test_broad_return_selectors_preserve_all_actions(
    context: ExecutionContext, returns: Any
) -> None:
    operand = build_return_context(returns, context)

    assert operand is context
    # These selectors are valid expressions, not just extraction failures.
    eval_templated_object(returns, operand=await materialize_context(operand))


def test_invalid_expression_is_left_to_activity(context: ExecutionContext) -> None:
    assert build_return_context("${{ ACTIONS.first.result + }}", context) is context


def test_external_result_stays_a_reference(context: ExecutionContext) -> None:
    stored = ExternalObject(
        ref=ObjectRef(
            bucket="test-workflow-results",
            key="test/result.json",
            size_bytes=3 * 1024 * 1024,
            sha256="0" * 64,
        ),
        typename="dict",
    )
    context["ACTIONS"]["first"] = TaskResult(result=stored, result_typename="dict")

    operand = build_return_context("${{ ACTIONS.first.result }}", context)

    assert operand["ACTIONS"]["first"].result is stored


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
