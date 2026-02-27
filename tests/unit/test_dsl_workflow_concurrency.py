from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionRetryPolicy,
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    TaskResult,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.dsl.workflow_logging import get_workflow_logger
from tracecat.storage.object import InlineObject
from tracecat.tiers.schemas import EffectiveLimits


def _effective_limits(
    *,
    max_action_executions_per_workflow: int | None = None,
    max_concurrent_actions: int | None = None,
) -> EffectiveLimits:
    return EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=max_action_executions_per_workflow,
        max_concurrent_actions=max_concurrent_actions,
    )


def _build_workflow(*, limits: EffectiveLimits | None = None) -> DSLWorkflow:
    workflow = object.__new__(DSLWorkflow)
    workflow.role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    workflow.logger = get_workflow_logger()
    workflow.runtime_config = DSLConfig()
    workflow._tier_limits = limits
    workflow._workflow_permit_acquired = False
    workflow._workflow_permit_heartbeat_task = None
    workflow._action_execution_count = 0
    context = ExecutionContext(ACTIONS={}, TRIGGER=None)
    workflow.context = context
    workflow.scheduler = cast(Any, SimpleNamespace(streams={ROOT_STREAM: context}))
    return workflow


@pytest.mark.anyio
async def test_retry_until_counts_action_execution_limit_per_iteration() -> None:
    workflow = _build_workflow(
        limits=_effective_limits(max_action_executions_per_workflow=3)
    )
    task = ActionStatement(
        ref="retry_action",
        action="core.transform.reshape",
        retry_policy=ActionRetryPolicy(
            retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}"
        ),
    )
    attempts = [
        TaskResult.from_result({"status": "loading"}),
        TaskResult.from_result({"status": "loading"}),
        TaskResult.from_result({"status": "success"}),
    ]

    with (
        patch.object(workflow, "_execute_task", new=AsyncMock(side_effect=attempts)),
        patch.object(workflow, "_set_logical_time_context", return_value=None),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=AsyncMock(side_effect=[False, False, True]),
        ),
    ):
        result = await workflow.execute_task(task)

    assert result.get_data() == {"status": "success"}
    assert workflow._action_execution_count == 3


@pytest.mark.anyio
async def test_retry_until_enforces_action_execution_limit() -> None:
    workflow = _build_workflow(
        limits=_effective_limits(max_action_executions_per_workflow=2)
    )
    task = ActionStatement(
        ref="retry_action",
        action="core.transform.reshape",
        retry_policy=ActionRetryPolicy(
            retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}"
        ),
    )

    execute_task_mock = AsyncMock(
        side_effect=[
            TaskResult.from_result({"status": "loading"}),
            TaskResult.from_result({"status": "loading"}),
            TaskResult.from_result({"status": "success"}),
        ]
    )

    with (
        patch.object(workflow, "_execute_task", new=execute_task_mock),
        patch.object(workflow, "_set_logical_time_context", return_value=None),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=AsyncMock(side_effect=[False, False, True]),
        ),
    ):
        with pytest.raises(
            ApplicationError,
            match="Action execution limit exceeded",
        ):
            await workflow.execute_task(task)

    assert execute_task_mock.await_count == 2
    assert workflow._action_execution_count == 3


@pytest.mark.anyio
async def test_execute_task_handles_timers_before_action_permit_acquisition() -> None:
    workflow = _build_workflow(limits=_effective_limits(max_concurrent_actions=1))
    task = ActionStatement(
        ref="delayed_action",
        action="core.transform.reshape",
        start_delay=30,
    )
    events: list[str] = []
    acquire_mock = AsyncMock(side_effect=lambda **_: events.append("acquire"))

    with (
        patch.object(
            workflow,
            "_handle_timers",
            new=AsyncMock(side_effect=lambda _: events.append("timers")),
        ),
        patch.object(workflow, "_action_permit_id", return_value="permit-id"),
        patch.object(workflow, "_acquire_action_permit", new=acquire_mock),
        patch.object(
            workflow, "_action_permit_heartbeat_loop", new=AsyncMock(return_value=None)
        ),
        patch.object(
            workflow,
            "_run_action",
            new=AsyncMock(return_value=InlineObject(data={"ok": True})),
        ),
        patch.object(
            workflow, "_release_action_permit", new=AsyncMock(return_value=None)
        ),
    ):
        result = await workflow._execute_task(task)

    assert events[:2] == ["timers", "acquire"]
    assert acquire_mock.await_count == 1
    assert result.get_data() == {"ok": True}


def test_resolve_child_loop_batch_size_rejects_non_positive_max_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _build_workflow()
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_MAX_IN_FLIGHT", 0)

    with pytest.raises(
        ApplicationError,
        match="TRACECAT__CHILD_WORKFLOW_MAX_IN_FLIGHT must be greater than 0",
    ):
        workflow._resolve_child_loop_batch_size(total_count=3, requested_batch_size=3)


def test_resolve_child_loop_batch_size_rejects_non_positive_tier_action_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _build_workflow(limits=_effective_limits(max_concurrent_actions=0))
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_MAX_IN_FLIGHT", 5)

    with pytest.raises(
        ApplicationError,
        match="max_concurrent_actions must be greater than 0",
    ):
        workflow._resolve_child_loop_batch_size(total_count=3, requested_batch_size=3)


def test_resolve_child_loop_batch_size_applies_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _build_workflow(limits=_effective_limits(max_concurrent_actions=2))
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_MAX_IN_FLIGHT", 4)

    strategy_batch_size, batch_size = workflow._resolve_child_loop_batch_size(
        total_count=10,
        requested_batch_size=6,
    )

    assert strategy_batch_size == 6
    assert batch_size == 2


def test_next_permit_heartbeat_sleep_seconds_applies_jitter() -> None:
    workflow = _build_workflow()

    with patch("tracecat.dsl.workflow.workflow.random") as random_mock:
        random_mock.return_value.uniform.return_value = 1.05
        sleep_seconds = workflow._next_permit_heartbeat_sleep_seconds(
            heartbeat_interval=60.0
        )

    assert sleep_seconds == pytest.approx(63.0)


@pytest.mark.anyio
async def test_run_cancellation_safe_cleanup_completes_after_cancellation() -> None:
    workflow = _build_workflow()
    started = asyncio.Event()
    completed = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        await asyncio.sleep(0)
        completed.set()

    cleanup_task = asyncio.create_task(
        workflow._run_cancellation_safe_cleanup(
            cleanup(),
            operation="test_cleanup",
        )
    )
    await started.wait()
    cleanup_task.cancel()
    await cleanup_task

    assert completed.is_set()


@pytest.mark.anyio
async def test_release_workflow_permit_is_idempotent() -> None:
    workflow = _build_workflow()
    workflow._workflow_permit_acquired = True
    execute_activity_mock = AsyncMock(return_value=None)

    with (
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=execute_activity_mock,
        ),
        patch(
            "tracecat.dsl.workflow.workflow.info",
            return_value=SimpleNamespace(workflow_id="wf-id"),
        ),
    ):
        await workflow._release_workflow_permit()
        await workflow._release_workflow_permit()

    assert execute_activity_mock.await_count == 1
    assert workflow._workflow_permit_acquired is False
