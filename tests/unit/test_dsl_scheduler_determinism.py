from __future__ import annotations

import asyncio

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import EdgeType
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import ROOT_STREAM, ActionStatement, ExecutionContext
from tracecat.dsl.types import Task


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
    scheduler = DSLScheduler(
        executor=executor,
        dsl=dsl,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
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
