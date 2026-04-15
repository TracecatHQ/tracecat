from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.scheduler import DSLScheduler
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    ExecutionContext,
    RunContext,
)
from tracecat.dsl.types import Task
from tracecat.identifiers.workflow import WorkflowUUID


@pytest.mark.anyio
async def test_task_should_skip_raises_for_unresolved_run_if_actions() -> None:
    async def executor(_: ActionStatement) -> None:
        return None

    dsl = DSLInput(
        title="test",
        description="test",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="guard", action="core.noop"),
            ActionStatement(
                ref="target",
                action="core.noop",
                depends_on=["a"],
                run_if="${{ ACTIONS.guard.result }}",
            ),
        ],
    )
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
    scheduler = DSLScheduler(
        executor=executor,
        dsl=dsl,
        max_pending_tasks=16,
        context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        role=role,
        run_context=run_context,
    )

    task = Task(ref="target", stream_id=ROOT_STREAM)
    stmt = scheduler.tasks[task.ref]
    with pytest.raises(
        ApplicationError,
        match=r"unresolved action reference\(s\).*'guard'",
    ):
        await scheduler._task_should_skip(task, stmt)
