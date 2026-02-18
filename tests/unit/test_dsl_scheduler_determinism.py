from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import EdgeType
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    ExecutionContext,
    RunContext,
    TaskResult,
)
from tracecat.dsl.types import Task
from tracecat.identifiers.workflow import WorkflowUUID


class _ControlledPutQueue:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self.items: list[Task] = []

    def _event(self, ref: str) -> asyncio.Event:
        event = self._events.get(ref)
        if event is None:
            event = asyncio.Event()
            self._events[ref] = event
        return event

    def release(self, ref: str) -> None:
        self._event(ref).set()

    async def put(self, item: Task) -> None:
        await self._event(item.ref).wait()
        self.items.append(item)

    def qsize(self) -> int:
        return len(self.items)


@pytest.mark.anyio
async def test_queue_tasks_is_deterministic() -> None:
    async def executor(_: ActionStatement) -> None:
        return None

    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            # Intentionally out of order; scheduler should queue in ref order.
            ActionStatement(ref="c", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
        ],
    )
    wf_id = WorkflowUUID.new_uuid4()
    test_role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    test_run_context = RunContext(
        wf_id=wf_id,
        wf_exec_id=f"{wf_id.short()}/exec_test",
        wf_run_id=uuid.uuid4(),
        environment="test",
        logical_time=datetime.now(UTC),
    )
    scheduler = DSLScheduler(
        executor=executor,
        dsl=dsl,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=test_role,
        run_context=test_run_context,
    )

    assert scheduler.adj["a"] == (("b", EdgeType.SUCCESS), ("c", EdgeType.SUCCESS))

    queue = _ControlledPutQueue()
    scheduler.queue = queue  # type: ignore[assignment]

    queue_task = asyncio.create_task(
        scheduler._queue_tasks(Task(ref="a", stream_id=ROOT_STREAM))
    )
    await asyncio.sleep(0)
    queue.release("c")
    await asyncio.sleep(0)
    queue.release("b")
    await queue_task

    assert [t.ref for t in queue.items] == ["b", "c"]


def _build_scheduler_for_run_if() -> DSLScheduler:
    async def executor(_: ActionStatement) -> None:
        return None

    dsl = DSLInput(
        title="run-if-plan-test",
        description="run-if-plan-test",
        entrypoint=DSLEntrypoint(ref="source_a"),
        actions=[
            ActionStatement(ref="source_a", action="core.noop"),
            ActionStatement(ref="source_b", action="core.noop"),
            ActionStatement(
                ref="target",
                action="core.noop",
                depends_on=["source_a", "source_b"],
                # Ensure dependency extraction is run_if-specific and not task-wide.
                args={"unused": "${{ ACTIONS.source_a.result }}"},
                run_if="${{ ACTIONS.source_b.result == '__OK__' }}",
            ),
        ],
    )
    wf_id = WorkflowUUID.new_uuid4()
    test_role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    test_run_context = RunContext(
        wf_id=wf_id,
        wf_exec_id=f"{wf_id.short()}/exec_test",
        wf_run_id=uuid.uuid4(),
        environment="test",
        logical_time=datetime.now(UTC),
    )
    return DSLScheduler(
        executor=executor,
        dsl=dsl,
        context=ExecutionContext(
            ACTIONS={
                "source_a": TaskResult.from_result("__UNUSED__"),
                "source_b": TaskResult.from_result("__OK__"),
            },
            TRIGGER=None,
            VARS={"x": "y"},
        ),
        role=test_role,
        run_context=test_run_context,
    )


def test_run_if_plan_extracts_only_run_if_dependencies() -> None:
    scheduler = _build_scheduler_for_run_if()
    plan = scheduler._run_if_plans["target"]
    assert plan.action_refs == ("source_b",)


def test_build_run_if_context_only_resolves_run_if_action_refs() -> None:
    scheduler = _build_scheduler_for_run_if()
    stmt = scheduler.tasks["target"]
    context = scheduler.build_run_if_context(stmt, ROOT_STREAM)
    assert set(context["ACTIONS"]) == {"source_b"}
    assert context.get("VARS") == {"x": "y"}


@pytest.mark.anyio
async def test_task_should_skip_shadow_mode_uses_legacy_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _build_scheduler_for_run_if()
    stmt = scheduler.tasks["target"]
    task = Task(ref="target", stream_id=ROOT_STREAM)
    monkeypatch.setattr(config, "TRACECAT__DSL_RUN_IF_MINIMAL_CONTEXT_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__DSL_RUN_IF_SHADOW_COMPARE_ENABLED", True)

    calls: list[ExecutionContext] = []

    async def _fake_resolve(expression: str, context: ExecutionContext) -> bool:
        _ = expression
        calls.append(context)
        # First call is minimal path, second call is legacy path.
        return len(calls) == 1

    monkeypatch.setattr(scheduler, "resolve_expression", _fake_resolve)
    should_skip = await scheduler._task_should_skip(task, stmt)
    assert should_skip is True
