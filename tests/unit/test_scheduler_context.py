import asyncio

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput, create_default_execution_context
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    StreamID,
    TaskResult,
)
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.types import Task
from tracecat.expressions.common import ExprContext


@pytest.mark.asyncio
async def test_run_if_inherits_parent_actions(monkeypatch: pytest.MonkeyPatch):
    dsl = DSLInput(
        title="run_if inherits actions",
        description="Ensure run_if can access ancestor ACTIONS context",
        entrypoint=DSLEntrypoint(ref="upstream"),
        actions=[
            ActionStatement(
                ref="upstream",
                action="core.transform.reshape",
                args={"value": "parent"},
            ),
            ActionStatement(
                ref="child",
                action="core.transform.reshape",
                depends_on=["upstream"],
                run_if="${{ ACTIONS.upstream.result == 'parent' }}",
                args={"value": "child"},
            ),
        ],
    )

    async def fake_executor(_: ActionStatement) -> None:  # pragma: no cover - not invoked
        return None

    scheduler = DSLScheduler(
        executor=fake_executor,
        dsl=dsl,
        context=create_default_execution_context(),
    )

    scheduler.streams[ROOT_STREAM][ExprContext.ACTIONS]["upstream"] = TaskResult(
        result="parent", result_typename="str"
    )

    scatter_stream = StreamID.new("scatter", 0, base_stream_id=ROOT_STREAM)
    scheduler.stream_hierarchy[scatter_stream] = ROOT_STREAM
    scheduler.streams[scatter_stream] = {
        ExprContext.ACTIONS: {
            "scatter": TaskResult(result="item", result_typename="str")
        }
    }

    captured: dict[str, object] = {}

    async def fake_resolve(expr: str, context):
        captured["context"] = context
        return context[ExprContext.ACTIONS]["upstream"]["result"] == "parent"

    monkeypatch.setattr(scheduler, "resolve_expression", fake_resolve)

    task = Task(ref="child", stream_id=scatter_stream)
    should_skip = await scheduler._task_should_skip(task, scheduler.tasks["child"])

    assert should_skip is False
    inherited_actions = captured["context"][ExprContext.ACTIONS]
    assert inherited_actions["upstream"]["result"] == "parent"
    assert inherited_actions["scatter"]["result"] == "item"
