"""Unit tests for the retry_until iteration cap classification steel thread.

The end-to-end behavior is covered by
``tests/temporal/test_workflow_timers.py::test_workflow_retry_until_rejects_over_iteration_cap``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.dsl.schemas import (
    ActionRetryPolicy,
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    RunContext,
    TaskResult,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import extract_error_classification


def _build_workflow() -> DSLWorkflow:
    workflow = object.__new__(DSLWorkflow)
    workflow.role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    assert workflow.role.organization_id is not None
    workflow.organization_id = workflow.role.organization_id
    workflow.logger = AsyncMock()
    workflow.runtime_config = DSLConfig()
    workflow._tier_limits = None
    workflow._action_execution_count = 0
    workflow.run_context = RunContext(
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000001"),
        wf_exec_id=(
            "wf-00000000000000000000000000000001:exec-00000000000000000000000000000001"
        ),
        wf_run_id=uuid.uuid4(),
        environment="__TEST__",
        logical_time=datetime.now(UTC),
    )
    workflow.context = ExecutionContext(ACTIONS={}, TRIGGER=None)
    workflow.wf_exec_id = workflow.run_context.wf_exec_id
    return workflow


def _retry_until_task() -> ActionStatement:
    return ActionStatement(
        ref="retry_action",
        action="core.transform.reshape",
        retry_policy=ActionRetryPolicy(
            retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}"
        ),
    )


async def _run_until_condition(
    workflow: DSLWorkflow,
    attempts: list[TaskResult],
    *,
    evaluate_results: list[bool],
) -> tuple[TaskResult | None, AsyncMock, AsyncMock]:
    """Drive ``_execute_task_until_condition`` with stubbed collaborators.

    Returns the task result plus the execute-task and expression-evaluation
    stubs so tests can assert how many iterations ran.
    """
    task = _retry_until_task()
    execute_task = AsyncMock(side_effect=attempts)
    evaluate_activity = AsyncMock(side_effect=evaluate_results)
    with (
        patch.object(workflow, "_execute_task", new=execute_task),
        patch.object(workflow, "_set_logical_time_context", return_value=None),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=evaluate_activity,
        ),
    ):
        return (
            await workflow._execute_task_until_condition(task),
            execute_task,
            evaluate_activity,
        )


@pytest.mark.anyio
async def test_retry_until_cap_is_user_attributed_non_retryable_loop_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting the cap raises a classified, non-retryable loop-limit error."""
    import tracecat.dsl.workflow as dsl_workflow_module

    workflow = _build_workflow()
    low_cap = 3
    monkeypatch.setattr(dsl_workflow_module, "MAX_RETRY_UNTIL_ITERATIONS", low_cap)

    attempts = [TaskResult.from_result({"status": "loading"}) for _ in range(low_cap)]

    with pytest.raises(ApplicationError) as exc_info:
        await _run_until_condition(
            workflow,
            attempts,
            evaluate_results=[False] * low_cap,
        )

    cause = exc_info.value
    assert cause.non_retryable is True
    assert "exceeded the retry_until iteration limit" in str(cause)
    assert str(low_cap) in str(cause)

    classification = extract_error_classification(cause)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.WORKFLOW_LOOP_LIMIT_EXCEEDED
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE


@pytest.mark.anyio
async def test_retry_until_success_within_cap_returns_satisfied_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A condition satisfied inside the cap returns that iteration's result."""
    import tracecat.dsl.workflow as dsl_workflow_module

    workflow = _build_workflow()
    low_cap = 3
    monkeypatch.setattr(dsl_workflow_module, "MAX_RETRY_UNTIL_ITERATIONS", low_cap)

    attempts = [
        TaskResult.from_result({"status": "loading"}),
        TaskResult.from_result({"status": "success"}),
    ]

    result, execute_task, evaluate_activity = await _run_until_condition(
        workflow,
        attempts,
        evaluate_results=[False, True],
    )

    assert result is not None
    assert result.get_data() == {"status": "success"}
    assert execute_task.await_count == 2
    assert evaluate_activity.await_count == 2
