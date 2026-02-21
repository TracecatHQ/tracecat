from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import ActionStatement, ExecutionContext, RunContext
from tracecat.identifiers.workflow import WorkflowUUID


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
    scheduler = DSLScheduler(
        executor=executor,
        dsl=dsl,
        max_pending_tasks=max_pending_tasks,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=test_role,
        run_context=test_run_context,
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
