from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.action import DSLActivities, FinalizeGatherActivityResult
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import EdgeType, PlatformAction
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
from tracecat.storage.object import InlineObject


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
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
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
        max_pending_tasks=16,
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


@pytest.mark.anyio
async def test_downstream_pin_force_skips_exclusive_upstream() -> None:
    async def executor(_: ActionStatement) -> None:
        return None

    dsl = DSLInput(
        title="pin-test",
        description="pin-test",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
        ],
    )
    wf_id = WorkflowUUID.new_uuid4()
    test_role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
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
        max_pending_tasks=16,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=test_role,
        run_context=test_run_context,
        pinned_action_results={"c": TaskResult.from_result({"value": "pinned"})},
    )

    assert scheduler.force_skip_refs == frozenset({"a", "b"})


@pytest.mark.anyio
async def test_downstream_pin_reuses_result_without_executing_upstream() -> None:
    """Invariant: reachable force-skipped ancestors still permit pin reuse."""
    executed_refs: list[str] = []

    async def executor(stmt: ActionStatement) -> None:
        executed_refs.append(stmt.ref)

    dsl = DSLInput(
        title="pin-run-test",
        description="pin-run-test",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
        ],
    )
    wf_id = WorkflowUUID.new_uuid4()
    test_role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
    )
    pinned_result = TaskResult.from_result({"value": "pinned"})
    context = ExecutionContext(
        ACTIONS={"c": pinned_result},
        TRIGGER=None,
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
        max_pending_tasks=16,
        context=context,
        role=test_role,
        run_context=test_run_context,
        pinned_action_results={"c": pinned_result},
    )

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == []
    assert scheduler.get_context(ROOT_STREAM)["ACTIONS"]["c"].get_data() == {
        "value": "pinned"
    }


def _make_pinned_scheduler(
    dsl: DSLInput,
    pinned_action_results: dict[str, TaskResult],
    executed_refs: list[str],
    *,
    raise_for_refs: set[str] | None = None,
) -> DSLScheduler:
    async def executor(stmt: ActionStatement) -> None:
        executed_refs.append(stmt.ref)
        if raise_for_refs is not None and stmt.ref in raise_for_refs:
            raise Exception(f"Failed {stmt.ref}")

    wf_id = WorkflowUUID.new_uuid4()
    role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-runner"],
    )
    run_context = RunContext(
        wf_id=wf_id,
        wf_exec_id=f"{wf_id.short()}/exec_test",
        wf_run_id=uuid.uuid4(),
        environment="test",
        logical_time=datetime.now(UTC),
    )
    # Mirror DSLWorkflow, which pre-seeds ACTIONS with the pinned results.
    context = ExecutionContext(
        ACTIONS={
            ref: result.model_copy(deep=True)
            for ref, result in pinned_action_results.items()
        },
        TRIGGER=None,
    )
    return DSLScheduler(
        executor=executor,
        dsl=dsl,
        max_pending_tasks=16,
        context=context,
        role=role,
        run_context=run_context,
        pinned_action_results=pinned_action_results,
    )


@pytest.mark.anyio
async def test_error_edge_parent_runs_when_pinned_handler_is_inactive() -> None:
    """Invariant: a conditional-edge predecessor stays live to select its branch."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-inactive-only-error-branch",
        description="pin-inactive-only-error-branch",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="handler", action="core.noop", depends_on=["a.error"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-handler"})
    scheduler = _make_pinned_scheduler(dsl, {"handler": pinned}, executed_refs)

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert "a" not in scheduler.force_skip_refs
    assert executed_refs == ["a"]
    assert "handler" not in scheduler.get_context(ROOT_STREAM)["ACTIONS"]
    assert scheduler.skipped_pinned_refs == ["handler"]


@pytest.mark.anyio
async def test_error_edge_parent_runs_when_pinned_handler_is_active() -> None:
    """Invariant: a live failing predecessor activates its pinned error handler."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-active-only-error-branch",
        description="pin-active-only-error-branch",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="handler", action="core.noop", depends_on=["a.error"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-handler"})
    scheduler = _make_pinned_scheduler(
        dsl,
        {"handler": pinned},
        executed_refs,
        raise_for_refs={"a"},
    )

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert "a" not in scheduler.force_skip_refs
    assert executed_refs == ["a"]
    assert scheduler.get_context(ROOT_STREAM)["ACTIONS"]["handler"].get_data() == {
        "value": "pinned-handler"
    }
    assert scheduler.skipped_pinned_refs == []


@pytest.mark.anyio
async def test_pinned_error_handler_is_not_reused_after_parent_success() -> None:
    """Invariant: a pin on an inactive error branch is skipped, not reused."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-inactive-error-branch",
        description="pin-inactive-error-branch",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="handler", action="core.noop", depends_on=["a.error"]),
            ActionStatement(ref="d", action="core.noop", depends_on=["a"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-handler"})
    scheduler = _make_pinned_scheduler(dsl, {"handler": pinned}, executed_refs)

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == ["a", "d"]
    assert "handler" not in scheduler.get_context(ROOT_STREAM)["ACTIONS"]
    assert scheduler.skipped_pinned_refs == ["handler"]


@pytest.mark.anyio
async def test_pinned_child_is_not_reused_after_parent_self_skips() -> None:
    """Invariant: a pin downstream of a run_if-false parent is skipped."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-after-self-skip",
        description="pin-after-self-skip",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(
                ref="b",
                action="core.noop",
                depends_on=["a"],
                run_if="${{ False }}",
            ),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
            ActionStatement(ref="d", action="core.noop", depends_on=["b"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    async def always_false(_expression: str, _context: ExecutionContext) -> bool:
        return False

    scheduler.resolve_expression = always_false  # type: ignore[method-assign]

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert "b" not in scheduler.force_skip_refs
    assert executed_refs == ["a"]
    assert "c" not in scheduler.get_context(ROOT_STREAM)["ACTIONS"]
    assert scheduler.skipped_pinned_refs == ["c"]


def _fan_in_dsl() -> DSLInput:
    """a -> b -> c, a -> d, and c + d -> e."""
    return DSLInput(
        title="pin-fan-in",
        description="pin-fan-in",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
            ActionStatement(ref="d", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="e", action="core.noop", depends_on=["c", "d"]),
        ],
    )


@pytest.mark.anyio
async def test_pin_on_one_fan_in_branch_keeps_join_reachable() -> None:
    """Invariant: a pinned task succeeds even though its only parent was
    force-skipped, so a join that also depends on a live branch still runs."""
    executed_refs: list[str] = []
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(_fan_in_dsl(), {"c": pinned}, executed_refs)

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert scheduler.force_skip_refs == frozenset({"b"})
    assert executed_refs == ["a", "d", "e"]
    assert scheduler.get_context(ROOT_STREAM)["ACTIONS"]["c"].get_data() == {
        "value": "pinned-c"
    }


@pytest.mark.anyio
async def test_pinning_every_fan_in_parent_runs_only_the_join() -> None:
    """Invariant: when every parent of a join is pinned, the whole upstream
    cone is force-skipped and the join still executes with the pinned inputs."""
    executed_refs: list[str] = []
    pins = {
        "c": TaskResult.from_result({"value": "pinned-c"}),
        "d": TaskResult.from_result({"value": "pinned-d"}),
    }
    scheduler = _make_pinned_scheduler(_fan_in_dsl(), pins, executed_refs)

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert scheduler.force_skip_refs == frozenset({"a", "b"})
    assert executed_refs == ["e"]


@pytest.mark.anyio
async def test_root_pin_after_scatter_returns_to_root_stream() -> None:
    """A downstream root pin must not send a scatter through its skip stream."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-after-scatter",
        description="pin-after-scatter",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action=PlatformAction.TRANSFORM_SCATTER,
                args={"collection": ["item"]},
            ),
            ActionStatement(ref="body", action="core.noop", depends_on=["scatter"]),
            ActionStatement(
                ref="gather",
                action=PlatformAction.TRANSFORM_GATHER,
                args={"items": "${{ ACTIONS.body.result }}"},
                depends_on=["body"],
            ),
            ActionStatement(ref="pinned", action="core.noop", depends_on=["gather"]),
            ActionStatement(ref="tail", action="core.noop", depends_on=["pinned"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned"})
    scheduler = _make_pinned_scheduler(dsl, {"pinned": pinned}, executed_refs)

    async def execute_activity(activity: object, *_: object, **__: object) -> object:
        if activity == DSLActivities.handle_scatter_input_activity:
            return InlineObject(data=["item"], typename="list")
        if activity == DSLActivities.finalize_gather_activity:
            return FinalizeGatherActivityResult(
                result=InlineObject(data=[], typename="list")
            )
        raise AssertionError(f"Unexpected activity: {activity}")

    with patch(
        "tracecat.dsl.scheduler.workflow.execute_activity",
        new=AsyncMock(side_effect=execute_activity),
    ):
        task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert scheduler.force_skip_refs == frozenset({"body", "gather"})
    assert executed_refs == ["gather", "tail"]
    assert scheduler.get_context(ROOT_STREAM)["ACTIONS"]["pinned"].get_data() == {
        "value": "pinned"
    }


@pytest.mark.anyio
async def test_pinned_task_with_false_run_if_self_skips_and_drops_result() -> None:
    """Invariant: a pinned task still honours `run_if`. When it self-skips it
    leaves no result in ACTIONS and reports the ref so read APIs do not stitch it."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-run-if",
        description="pin-run-if",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(
                ref="c",
                action="core.noop",
                depends_on=["b"],
                run_if="${{ FN.is_null(TRIGGER) == False }}",
            ),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    async def always_false(_expression: str, _context: ExecutionContext) -> bool:
        return False

    scheduler.resolve_expression = always_false  # type: ignore[method-assign]

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == []
    assert scheduler.skipped_pinned_refs == ["c"]
    assert "c" not in scheduler.get_context(ROOT_STREAM)["ACTIONS"]


@pytest.mark.anyio
async def test_force_skipped_false_guard_does_not_bypass_pinned_child() -> None:
    """Invariant: a guarded ancestor that self-skips cannot enable pin reuse."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-after-force-skipped-false-guard",
        description="pin-after-force-skipped-false-guard",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(
                ref="b",
                action="core.noop",
                depends_on=["a"],
                run_if="${{ False }}",
            ),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    async def always_false(_expression: str, _context: ExecutionContext) -> bool:
        return False

    scheduler.resolve_expression = always_false  # type: ignore[method-assign]

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == []
    assert "c" not in scheduler.get_context(ROOT_STREAM)["ACTIONS"]
    assert scheduler.skipped_pinned_refs == ["c"]


@pytest.mark.anyio
async def test_force_skipped_unavailable_guard_keeps_pinned_child_reachable() -> None:
    """Invariant: an unavailable force-skipped guard fails open for pin reuse."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-after-force-skipped-unavailable-guard",
        description="pin-after-force-skipped-unavailable-guard",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(
                ref="b",
                action="core.noop",
                depends_on=["a"],
                run_if="${{ ACTIONS.a.result }}",
            ),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert executed_refs == []
    assert scheduler.get_context(ROOT_STREAM)["ACTIONS"]["c"].get_data() == {
        "value": "pinned-c"
    }
    assert scheduler.skipped_pinned_refs == []


@pytest.mark.anyio
async def test_force_skipped_guard_propagates_unrelated_error() -> None:
    """A real ancestor guard failure must not count as a successful bypass."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-after-force-skipped-guard-error",
        description="pin-after-force-skipped-guard-error",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(
                ref="b",
                action="core.noop",
                depends_on=["a"],
                run_if="${{ SECRETS.missing.VALUE }}",
            ),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    async def guard_error(_expression: str, _context: ExecutionContext) -> bool:
        raise ApplicationError("Secret lookup failed")

    scheduler.resolve_expression = guard_error  # type: ignore[method-assign]

    task_exceptions = await scheduler.start()

    assert task_exceptions is not None
    assert "b" in task_exceptions
    assert executed_refs == []


@pytest.mark.anyio
async def test_pinned_task_with_unavailable_upstream_guard_reuses_result() -> None:
    """Invariant: a pinned task whose guard references an unavailable
    force-skipped upstream reuses the pin instead of failing."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-run-if-unavailable-upstream",
        description="pin-run-if-unavailable-upstream",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(
                ref="c",
                action="core.noop",
                depends_on=["b"],
                run_if="${{ ACTIONS.b.result }}",
            ),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    task_exceptions = await scheduler.start()

    assert task_exceptions is None
    assert "b" not in executed_refs
    assert scheduler.get_context(ROOT_STREAM)["ACTIONS"]["c"].get_data() == {
        "value": "pinned-c"
    }


@pytest.mark.anyio
async def test_pinned_task_propagates_unrelated_run_if_error() -> None:
    """A real guard failure must not be converted into successful pin reuse."""
    executed_refs: list[str] = []
    dsl = DSLInput(
        title="pin-run-if-error",
        description="pin-run-if-error",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(
                ref="c",
                action="core.noop",
                depends_on=["b"],
                run_if="${{ SECRETS.missing.VALUE }}",
            ),
        ],
    )
    pinned = TaskResult.from_result({"value": "pinned-c"})
    scheduler = _make_pinned_scheduler(dsl, {"c": pinned}, executed_refs)

    async def guard_error(_expression: str, _context: ExecutionContext) -> bool:
        raise ApplicationError("Secret lookup failed")

    scheduler.resolve_expression = guard_error  # type: ignore[method-assign]

    task_exceptions = await scheduler.start()

    assert task_exceptions is not None
    assert "c" in task_exceptions
    assert executed_refs == []
