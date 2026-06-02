from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from tracecat.auth.types import Role
from tracecat.dsl import scheduler as scheduler_module
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import ActionStatement, ExecutionContext, RunContext
from tracecat.identifiers.workflow import WorkflowUUID


def _build_scheduler(
    *,
    total_tasks: int,
    executor: Callable[[ActionStatement], Awaitable[None]],
    max_pending_tasks: int | None = None,
) -> DSLScheduler:
    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref="task_0"),
        actions=[
            ActionStatement(ref=f"task_{index}", action="core.noop")
            for index in range(total_tasks)
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
        max_pending_tasks=max_pending_tasks or total_tasks,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=test_role,
        run_context=test_run_context,
    )


@pytest.mark.anyio
async def test_scheduler_respects_max_pending_tasks_cap() -> None:
    max_pending_tasks = 3
    total_tasks = 10
    active_tasks = 0
    started_tasks = 0
    max_active_tasks = 0
    cap_reached = asyncio.Event()
    release_tasks = asyncio.Event()

    async def executor(_: ActionStatement) -> None:
        nonlocal active_tasks, max_active_tasks, started_tasks
        started_tasks += 1
        active_tasks += 1
        max_active_tasks = max(max_active_tasks, active_tasks)
        if max_active_tasks == max_pending_tasks:
            cap_reached.set()
        await release_tasks.wait()
        active_tasks -= 1

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        executor=executor,
        max_pending_tasks=max_pending_tasks,
    )

    scheduler_task = asyncio.create_task(scheduler.start())
    await asyncio.wait_for(cap_reached.wait(), timeout=2)
    await asyncio.sleep(0)

    assert max_active_tasks == max_pending_tasks
    assert active_tasks == max_pending_tasks

    release_tasks.set()
    result = await asyncio.wait_for(scheduler_task, timeout=2)

    assert result is None
    assert started_tasks == total_tasks
    assert max_active_tasks == max_pending_tasks


@pytest.mark.anyio
async def test_scheduler_yields_while_spawning_ready_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total_tasks = scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY + 1
    yield_count = 0
    original_sleep = asyncio.sleep

    async def spy_sleep(delay: float, result: object = None) -> object:
        nonlocal yield_count
        if delay == 0:
            yield_count += 1
        return await original_sleep(0, result)

    monkeypatch.setattr(scheduler_module.asyncio, "sleep", spy_sleep)

    async def executor(_: ActionStatement) -> None:
        await original_sleep(0)

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        max_pending_tasks=total_tasks,
        executor=executor,
    )

    result = await asyncio.wait_for(scheduler.start(), timeout=2)

    assert result is None
    assert yield_count >= 1


@pytest.mark.anyio
async def test_scheduler_reclaims_completed_tasks_after_spawn_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total_tasks = scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY + 1
    wait_count = 0
    original_wait = scheduler_module.workflow.wait

    async def spy_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal wait_count
        wait_count += 1
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(scheduler_module.workflow, "wait", spy_wait)

    started_refs: list[str] = []

    async def executor(stmt: ActionStatement) -> None:
        started_refs.append(stmt.ref)

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        max_pending_tasks=scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY,
        executor=executor,
    )

    result = await asyncio.wait_for(scheduler.start(), timeout=2)

    assert result is None
    assert started_refs == [f"task_{index}" for index in range(total_tasks)]
    assert wait_count == 1


@pytest.mark.anyio
async def test_scheduler_stops_spawning_after_failure_during_spawn_yield() -> None:
    total_tasks = scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY + 1
    started_refs: list[str] = []
    block_pending_tasks = asyncio.Event()

    async def executor(stmt: ActionStatement) -> None:
        started_refs.append(stmt.ref)
        if stmt.ref == "task_0":
            raise RuntimeError("intentional scheduler test failure")
        await block_pending_tasks.wait()

    scheduler = _build_scheduler(
        total_tasks=total_tasks,
        max_pending_tasks=total_tasks,
        executor=executor,
    )

    result = await asyncio.wait_for(scheduler.start(), timeout=2)

    assert result is not None
    first_unstarted_ref = f"task_{scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY}"
    assert len(started_refs) == scheduler_module._SCHEDULER_TASK_SPAWN_YIELD_EVERY
    assert first_unstarted_ref not in started_refs
