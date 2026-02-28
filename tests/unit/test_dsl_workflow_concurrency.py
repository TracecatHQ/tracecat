from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import FailStrategy, WaitStrategy
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionRetryPolicy,
    ActionStatement,
    DSLConfig,
    ExecutionContext,
    RunContext,
    TaskResult,
)
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.dsl.workflow_logging import get_workflow_logger
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage.object import InlineObject
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.workflow.executions.enums import ExecutionType


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
    workflow.execution_type = ExecutionType.PUBLISHED
    workflow.run_context = RunContext(
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000001"),
        wf_exec_id="wf-00000000000000000000000000000001:exec-00000000000000000000000000000001",
        wf_run_id=uuid.uuid4(),
        environment="__TEST__",
        logical_time=datetime.now(UTC),
    )
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


def test_resolve_child_loop_batch_plan_applies_dispatch_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _build_workflow()
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW", 12)

    logical_batch_size, dispatch_window = workflow._resolve_child_loop_batch_plan(
        total_count=10,
        requested_batch_size=6,
    )

    assert logical_batch_size == 6
    assert dispatch_window == 12


def test_resolve_child_loop_batch_plan_is_independent_of_tier_action_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _build_workflow(limits=_effective_limits(max_concurrent_actions=1))
    monkeypatch.setattr(config, "TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW", 13)

    logical_batch_size, dispatch_window = workflow._resolve_child_loop_batch_plan(
        total_count=8,
        requested_batch_size=8,
    )

    assert logical_batch_size == 8
    assert dispatch_window == 13


@pytest.mark.anyio
async def test_execute_child_workflow_batch_prepared_limits_dispatch_window() -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="run_child", action="core.workflow.execute", args={})
    dsl = DSLInput(
        title="Child",
        description="child workflow for unit test",
        entrypoint=DSLEntrypoint(ref="noop", expects={}),
        actions=[
            ActionStatement(
                ref="noop",
                action="core.transform.reshape",
                args={"value": "ok"},
            )
        ],
        triggers=[],
    )
    prepared = cast(
        Any,
        SimpleNamespace(
            dsl=dsl,
            wf_id="wf-00000000000000000000000000000001",
            registry_lock=None,
            get_config=lambda _idx: DSLConfig(),
            get_trigger_input_at=lambda idx: InlineObject(data={"index": idx}),
        ),
    )

    dispatch_in_flight = 0
    max_dispatch_in_flight = 0
    child_runs_in_flight = 0
    max_child_runs_in_flight = 0
    dispatch_lock = asyncio.Lock()
    child_runs_lock = asyncio.Lock()

    async def child_run(loop_index: int) -> InlineObject:
        nonlocal child_runs_in_flight, max_child_runs_in_flight
        async with child_runs_lock:
            child_runs_in_flight += 1
            max_child_runs_in_flight = max(
                max_child_runs_in_flight,
                child_runs_in_flight,
            )
        try:
            await asyncio.sleep(0.05)
            return InlineObject(data={"index": loop_index})
        finally:
            async with child_runs_lock:
                child_runs_in_flight -= 1

    async def dispatch_child_mock(
        _: ActionStatement,
        __: Any,
        *,
        wait_strategy: Any,
        loop_index: int | None = None,
    ) -> asyncio.Task[InlineObject]:
        del wait_strategy
        nonlocal dispatch_in_flight, max_dispatch_in_flight
        assert loop_index is not None
        async with dispatch_lock:
            dispatch_in_flight += 1
            max_dispatch_in_flight = max(max_dispatch_in_flight, dispatch_in_flight)
        try:
            await asyncio.sleep(0.01)
            return asyncio.create_task(child_run(loop_index))
        finally:
            async with dispatch_lock:
                dispatch_in_flight -= 1

    with patch.object(
        workflow,
        "_dispatch_child_workflow",
        new=AsyncMock(side_effect=dispatch_child_mock),
    ):
        result = await workflow._execute_child_workflow_batch_prepared(
            task=task,
            prepared=prepared,
            batch_start=0,
            batch_size=6,
            dispatch_window=2,
            wait_strategy=WaitStrategy.WAIT,
            fail_strategy=FailStrategy.ISOLATED,
            child_time_anchor=datetime.now(UTC),
        )

    assert max_dispatch_in_flight == 2
    assert max_child_runs_in_flight > 2
    assert [cast(InlineObject, val).data["index"] for val in result] == list(range(6))


@pytest.mark.anyio
async def test_run_child_workflow_defaults_wait_strategy_to_detach() -> None:
    workflow = _build_workflow()
    task = ActionStatement(ref="run_child", action="core.workflow.execute", args={})

    async def dispatch_child_mock(
        _: ActionStatement,
        __: Any,
        *,
        wait_strategy: WaitStrategy,
        loop_index: int | None = None,
    ) -> Any:
        del loop_index
        assert wait_strategy == WaitStrategy.DETACH
        return SimpleNamespace(id="wf_child/exec_default")

    with patch.object(
        workflow,
        "_dispatch_child_workflow",
        new=AsyncMock(side_effect=dispatch_child_mock),
    ):
        result = await workflow._run_child_workflow(task, run_args=cast(Any, object()))

    assert isinstance(result, InlineObject)
    assert result.data == "wf_child/exec_default"


@pytest.mark.anyio
async def test_run_child_workflow_wait_strategy_wait_returns_child_result() -> None:
    workflow = _build_workflow()
    task = ActionStatement(
        ref="run_child",
        action="core.workflow.execute",
        args={"wait_strategy": WaitStrategy.WAIT.value},
    )

    async def dispatch_child_mock(
        _: ActionStatement,
        __: Any,
        *,
        wait_strategy: WaitStrategy,
        loop_index: int | None = None,
    ) -> asyncio.Task[InlineObject]:
        del loop_index
        assert wait_strategy == WaitStrategy.WAIT

        async def child_result() -> InlineObject:
            return InlineObject(data={"status": "ok"})

        return asyncio.create_task(child_result())

    with patch.object(
        workflow,
        "_dispatch_child_workflow",
        new=AsyncMock(side_effect=dispatch_child_mock),
    ):
        result = await workflow._run_child_workflow(task, run_args=cast(Any, object()))

    assert isinstance(result, InlineObject)
    assert result.data == {"status": "ok"}


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
