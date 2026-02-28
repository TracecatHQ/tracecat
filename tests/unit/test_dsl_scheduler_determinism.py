from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import EdgeType
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    ExecutionContext,
    RunContext,
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
