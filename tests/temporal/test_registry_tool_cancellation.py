"""A cancelled MCP caller must not leave a queued tool runnable after recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType, IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import TypedSearchAttributes
from temporalio.exceptions import CancelledError as TemporalCancelledError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from tracecat_ee.agent.workflows.registry_tool import ExecuteRegistryToolWorkflow

from tracecat import config
from tracecat.agent.mcp import executor
from tracecat.agent.worker import new_sandbox_runner
from tracecat.agent.workflows.tool_execution import (
    ExecuteRegistryToolWorkflowInput,
    ExecuteRegistryToolWorkflowMemo,
)
from tracecat.auth.types import Role
from tracecat.dsl import interceptor as interceptor_module
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.interceptor import RuntimeErrorAttributionInterceptor
from tracecat.dsl.schemas import RunActionInput
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject, StoredObject
from tracecat.workflow.executions.enums import TemporalSearchAttr

pytestmark = [pytest.mark.temporal]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_cancellation_before_worker_start_prevents_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_queue = f"registry-tool-cancellation-{uuid4()}"
    workflow_id = f"agent-tool/{uuid4()}"
    calls: list[RunActionInput] = []

    @activity.defn(name="execute_action_activity")
    async def execute_action_activity(
        run_input: RunActionInput, role: Role
    ) -> StoredObject:
        calls.append(run_input)
        return InlineObject(data="unexpected execution")

    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", task_queue)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", task_queue)
    workflow_input = ExecuteRegistryToolWorkflowInput(
        role=Role(type="service", service_id="tracecat-mcp"),
        run_input=executor.build_run_input(
            "core.http_request",
            {"url": "https://example.com"},
            RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={"core.http_request": "tracecat_registry"},
            ),
        ),
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(),
    ) as env:
        monkeypatch.setattr(
            executor, "get_temporal_client", AsyncMock(return_value=env.client)
        )
        caller = asyncio.create_task(
            executor._execute_action_workflow(
                workflow_input,
                workflow_id=workflow_id,
                memo=ExecuteRegistryToolWorkflowMemo(
                    parent_agent_session_id=uuid4(), action_name="core.http_request"
                ),
                search_attributes=TypedSearchAttributes(search_attributes=[]),
            )
        )
        handle = env.client.get_workflow_handle_for(
            ExecuteRegistryToolWorkflow.run, workflow_id
        )
        try:
            async with asyncio.timeout(10):
                # Observe server acceptance without starting a workflow worker.
                while True:
                    try:
                        await handle.describe()
                        break
                    except RPCError as error:
                        if error.status != RPCStatusCode.NOT_FOUND:
                            raise
                        await asyncio.sleep(0.01)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller

            history = await handle.fetch_history()
            assert any(
                event.HasField("workflow_execution_cancel_requested_event_attributes")
                for event in history.events
            )
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[ExecuteRegistryToolWorkflow],
                activities=[execute_action_activity],
                workflow_runner=new_sandbox_runner(),
            ):
                with pytest.raises(WorkflowFailureError) as raised:
                    await asyncio.wait_for(handle.result(), timeout=10)
            assert isinstance(raised.value.cause, TemporalCancelledError)
            assert (await handle.describe()).status == WorkflowExecutionStatus.CANCELED
            assert not calls
            history = await handle.fetch_history()
            assert not any(
                event.HasField("activity_task_started_event_attributes")
                for event in history.events
            )
        finally:
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)


@pytest.mark.anyio
async def test_cancellation_during_tool_execution_preserves_cancelled_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_queue = f"registry-tool-cancellation-{uuid4()}"
    workflow_id = f"agent-tool/{uuid4()}"
    invocations: list[int] = []
    completed_side_effects: list[int] = []
    activity_started = asyncio.Event()
    activity_cancelled = asyncio.Event()

    @activity.defn(name="execute_action_activity")
    async def execute_action_activity(
        run_input: RunActionInput, role: Role
    ) -> StoredObject:
        del run_input, role
        invocation = len(invocations) + 1
        invocations.append(invocation)
        activity_started.set()
        activity.heartbeat("waiting for cancellation")
        if invocation == 1:
            try:
                while not activity.is_cancelled():
                    activity.heartbeat("waiting for cancellation")
                    await asyncio.sleep(0.05)
                activity_cancelled.set()
                raise TemporalCancelledError("activity cancelled")
            except asyncio.CancelledError:
                activity_cancelled.set()
                raise
        if invocation == 3:
            raise RuntimeError("synthetic activity failure")
        completed_side_effects.append(invocation)
        return InlineObject(data="later success")

    capture_platform_failure = Mock()
    monkeypatch.setattr(
        interceptor_module, "capture_platform_failure", capture_platform_failure
    )
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", task_queue)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", task_queue)
    workflow_input = ExecuteRegistryToolWorkflowInput(
        role=Role(type="service", service_id="tracecat-mcp"),
        run_input=executor.build_run_input(
            "core.http_request",
            {"url": "https://example.com"},
            RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={"core.http_request": "tracecat_registry"},
            ),
        ),
    )
    memo = ExecuteRegistryToolWorkflowMemo(
        parent_agent_session_id=uuid4(),
        action_name="core.http_request",
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(),
    ) as env:
        await env.client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                search_attributes={
                    TemporalSearchAttr.ERROR_OWNER.value: (
                        IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                    )
                }
            )
        )
        monkeypatch.setattr(
            executor, "get_temporal_client", AsyncMock(return_value=env.client)
        )
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ExecuteRegistryToolWorkflow],
            activities=[execute_action_activity],
            workflow_runner=new_sandbox_runner(),
            interceptors=[RuntimeErrorAttributionInterceptor()],
            max_heartbeat_throttle_interval=timedelta(milliseconds=100),
            default_heartbeat_throttle_interval=timedelta(milliseconds=50),
        ):
            caller = asyncio.create_task(
                executor._execute_action_workflow(
                    workflow_input,
                    workflow_id=workflow_id,
                    memo=memo,
                    search_attributes=TypedSearchAttributes(search_attributes=[]),
                )
            )
            handle = env.client.get_workflow_handle_for(
                ExecuteRegistryToolWorkflow.run, workflow_id
            )
            try:
                await asyncio.wait_for(activity_started.wait(), timeout=10)
                caller.cancel("caller left")
                with pytest.raises(asyncio.CancelledError, match="caller left"):
                    await caller

                with env.auto_time_skipping_disabled():
                    await asyncio.wait_for(activity_cancelled.wait(), timeout=10)

                with env.auto_time_skipping_disabled():
                    with pytest.raises(WorkflowFailureError) as raised:
                        await asyncio.wait_for(handle.result(), timeout=10)
                assert isinstance(raised.value.cause, TemporalCancelledError)
                assert (
                    await handle.describe()
                ).status == WorkflowExecutionStatus.CANCELED

                history = await handle.fetch_history()
                event_names = [
                    EventType.Name(event.event_type) for event in history.events
                ]
                assert event_names.count("EVENT_TYPE_ACTIVITY_TASK_SCHEDULED") == 1
                assert (
                    event_names.count("EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED") == 1
                )
                assert "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED" not in event_names
                assert invocations == [1]
                assert completed_side_effects == []
                capture_platform_failure.assert_not_called()

                later_result = await executor._execute_action_workflow(
                    workflow_input,
                    workflow_id=f"{workflow_id}-later",
                    memo=memo,
                    search_attributes=TypedSearchAttributes(search_attributes=[]),
                )
                assert later_result == InlineObject(data="later success")
                assert invocations == [1, 2]
                assert completed_side_effects == [2]

                with pytest.raises(executor.ActionExecutionError):
                    await executor._execute_action_workflow(
                        workflow_input,
                        workflow_id=f"{workflow_id}-failure",
                        memo=memo,
                        search_attributes=TypedSearchAttributes(search_attributes=[]),
                    )
                capture_platform_failure.assert_called_once()
            finally:
                if not caller.done():
                    caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
