"""Integration tests for agent approval workflow.

These tests verify the approval flow:
1. Tool approval requests trigger workflow pause
2. Workflow waits for approval signal via workflow update
3. Approved tools execute correctly via execute_approved_tools_activity
4. Rejected tools return denial messages
5. Session resumes correctly after approval

The tests mock `run_agent_activity` to return `approval_requested=True` which
triggers the workflow's approval handling logic.

Run via: uv run pytest tests/integration/test_agent_approval_flow.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

import pytest
from pydantic_ai.tools import ToolApproved, ToolDenied
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from tracecat_ee.agent.activities import AgentActivities
from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalService
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
)

from tracecat import config
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    ExecuteApprovedToolsInput,
    ExecuteApprovedToolsResult,
    ToolExecutionResult,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.activities import get_session_activities
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.feature_flags import FeatureFlag

# Use a unique test queue to avoid collision with docker-compose workers
TEST_AGENT_QUEUE = "test-agent-approval-queue"

pytestmark = pytest.mark.usefixtures("db")

# =============================================================================
# Mock Activity Factories
# =============================================================================


def create_mock_run_agent_activity(
    response_callback: Callable[[AgentExecutorInput, int], AgentExecutorResult],
) -> Callable[..., Any]:
    """Create a mock run_agent_activity with stateful responses.

    Args:
        response_callback: Function that takes (input, call_count) and returns
            AgentExecutorResult. Use call_count to vary behavior across turns.
    """
    call_count = 0

    @activity.defn(name="run_agent_activity")
    async def mock_run_agent_activity(
        input: AgentExecutorInput,
    ) -> AgentExecutorResult:
        nonlocal call_count
        activity.heartbeat("Mock agent running")
        result = response_callback(input, call_count)
        call_count += 1
        return result

    return mock_run_agent_activity


def create_mock_execute_approved_tools_activity(
    result_callback: Callable[[ExecuteApprovedToolsInput], ExecuteApprovedToolsResult]
    | None = None,
) -> Callable[..., Any]:
    """Create a mock execute_approved_tools_activity."""

    @activity.defn(name="execute_approved_tools_activity")
    async def mock_execute_approved_tools_activity(
        input: ExecuteApprovedToolsInput,
    ) -> ExecuteApprovedToolsResult:
        if result_callback:
            return result_callback(input)

        # Default: return success for all approved tools
        results = [
            ToolExecutionResult(
                tool_call_id=tool.tool_call_id,
                tool_name=tool.tool_name,
                result={"status": "success"},
                is_error=False,
            )
            for tool in input.approved_tools
        ]
        # Add denial messages for denied tools
        results.extend(
            ToolExecutionResult(
                tool_call_id=tool.tool_call_id,
                tool_name=tool.tool_name,
                result={"error": tool.reason},
                is_error=True,
            )
            for tool in input.denied_tools
        )
        return ExecuteApprovedToolsResult(results=results, success=True)

    return mock_execute_approved_tools_activity


def create_activities_with_mock_executor(
    response_callback: Callable[[AgentExecutorInput, int], AgentExecutorResult],
    tool_result_callback: Callable[
        [ExecuteApprovedToolsInput], ExecuteApprovedToolsResult
    ]
    | None = None,
) -> list[Callable[..., Any]]:
    """Create a full activity list with mocked executor activities."""
    agent_activities = AgentActivities()

    activities: list[Callable[..., Any]] = list(agent_activities.get_activities())
    activities.extend(get_session_activities())
    activities.extend(ApprovalManager.get_activities())
    activities.append(create_mock_run_agent_activity(response_callback))
    activities.append(create_mock_execute_approved_tools_activity(tool_result_callback))

    return activities


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def enable_agent_approvals_flag(monkeypatch):
    """Enable agent approvals feature flag for all tests in this module."""
    monkeypatch.setattr(
        config,
        "TRACECAT__FEATURE_FLAGS",
        {FeatureFlag.AGENT_APPROVALS},
    )


@pytest.fixture
def agent_config_with_approvals() -> AgentConfig:
    """Create agent configuration with tool approvals enabled."""
    return AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        namespaces=None,
        actions=["core.http_request"],
        tool_approvals={"core.http_request": True},
    )


@pytest.fixture
async def agent_worker_factory(threadpool):
    """Factory to create workers configured for agent workflows."""

    def create_agent_worker(
        client: Client,
        *,
        task_queue: str | None = None,
        custom_activities: Sequence[Callable[..., object]] | None = None,
    ) -> Worker:
        if custom_activities is not None:
            activities: Sequence[Callable[..., object]] = custom_activities
        else:
            agent_activities = AgentActivities()

            activities = [
                *agent_activities.get_activities(),
                *get_session_activities(),
                *ApprovalManager.get_activities(),
            ]

        return Worker(
            client=client,
            task_queue=task_queue or TEST_AGENT_QUEUE,
            activities=activities,
            workflows=[DurableAgentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activity_executor=threadpool,
        )

    yield create_agent_worker


# =============================================================================
# Test Classes
# =============================================================================


class TestAgentApprovalFlow:
    """Tests for agent approval handling."""

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_approval_request_persisted_to_db(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
    ):
        """Verify approval requests are persisted to the database."""
        queue = TEST_AGENT_QUEUE
        session_id = test_agent_session_a.id

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            if call_count == 0:
                # First call: request approval
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id="call_123",
                            name="core__http_request",
                            input={"url": "https://example.com", "method": "GET"},
                        )
                    ],
                )
            # After approval: complete successfully
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Make an HTTP request",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    workflow_args,
                    id=f"test-approval-persist-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for approval to be pending in DB
                tool_call_id = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc.list_approvals_for_session(session_id)
                        if approvals:
                            approval = approvals[0]
                            await svc.session.refresh(approval)
                            if approval.status == ApprovalStatus.PENDING:
                                tool_call_id = approval.tool_call_id
                                break

                # Verify approval was persisted
                assert tool_call_id is not None, "Approval was not persisted to DB"

                # Submit approval to allow workflow to complete
                submission = WorkflowApprovalSubmission(
                    approvals={tool_call_id: True},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission,
                )

                await wf_handle.result()
        finally:
            if wf_handle:
                try:
                    await wf_handle.cancel()
                except Exception:
                    pass

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_workflow_waits_for_approval(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
    ):
        """Verify workflow pauses when approval is needed."""
        queue = TEST_AGENT_QUEUE
        session_id = test_agent_session_a.id

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            if call_count == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id="call_wait_123",
                            name="core__http_request",
                            input={"url": "https://example.com", "method": "GET"},
                        )
                    ],
                )
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Make request needing approval",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    workflow_args,
                    id=f"test-approval-wait-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for approval to be pending
                tool_call_id = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc.list_approvals_for_session(session_id)
                        if approvals and approvals[0].status == ApprovalStatus.PENDING:
                            tool_call_id = approvals[0].tool_call_id
                            break

                assert tool_call_id is not None, "Workflow did not wait for approval"

                # Verify workflow is still running (waiting)
                desc = await wf_handle.describe()
                assert desc.status is not None
                assert desc.status.name == "RUNNING"

                # Now submit approval
                submission = WorkflowApprovalSubmission(
                    approvals={tool_call_id: True},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission,
                )

                result = await wf_handle.result()
                assert result.session_id == session_id
        finally:
            if wf_handle:
                try:
                    await wf_handle.cancel()
                except Exception:
                    pass

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_approved_tool_executes(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
    ):
        """Verify that approved tools execute correctly after approval."""
        queue = TEST_AGENT_QUEUE
        session_id = test_agent_session_a.id

        executed_tools: list[str] = []

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            if call_count == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id="call_exec_123",
                            name="core__http_request",
                            input={"url": "https://example.com", "method": "GET"},
                        )
                    ],
                )
            return AgentExecutorResult(success=True)

        def tool_result_callback(
            input: ExecuteApprovedToolsInput,
        ) -> ExecuteApprovedToolsResult:
            # Track which tools were executed
            for tool in input.approved_tools:
                executed_tools.append(tool.tool_name)

            return ExecuteApprovedToolsResult(
                results=[
                    ToolExecutionResult(
                        tool_call_id=tool.tool_call_id,
                        tool_name=tool.tool_name,
                        result={"status": "executed"},
                        is_error=False,
                    )
                    for tool in input.approved_tools
                ],
                success=True,
            )

        activities = create_activities_with_mock_executor(
            mock_executor, tool_result_callback
        )

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Execute approved tool",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    workflow_args,
                    id=f"test-approval-exec-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for approval
                tool_call_id = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc.list_approvals_for_session(session_id)
                        if approvals and approvals[0].status == ApprovalStatus.PENDING:
                            tool_call_id = approvals[0].tool_call_id
                            break

                assert tool_call_id is not None

                # Approve the tool
                submission = WorkflowApprovalSubmission(
                    approvals={tool_call_id: True},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission,
                )

                result = await wf_handle.result()
                assert result.session_id == session_id

                # Verify tool was executed
                assert "core__http_request" in executed_tools

                # Verify approval status is APPROVED
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    approval = await svc.get_approval_by_session_and_tool(
                        session_id=session_id, tool_call_id=tool_call_id
                    )
                    assert approval is not None
                    assert approval.status == ApprovalStatus.APPROVED
        finally:
            if wf_handle:
                try:
                    await wf_handle.cancel()
                except Exception:
                    pass

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_rejected_tool_returns_denial(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
    ):
        """Verify that rejected tools are recorded with denial message."""
        queue = TEST_AGENT_QUEUE
        session_id = test_agent_session_a.id

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            if call_count == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id="call_reject_123",
                            name="core__http_request",
                            input={"url": "https://dangerous.com", "method": "DELETE"},
                        )
                    ],
                )
            # After rejection, agent continues
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Execute risky operation",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    workflow_args,
                    id=f"test-approval-reject-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for approval
                tool_call_id = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc.list_approvals_for_session(session_id)
                        if approvals and approvals[0].status == ApprovalStatus.PENDING:
                            tool_call_id = approvals[0].tool_call_id
                            break

                assert tool_call_id is not None

                # Reject the tool with a message
                submission = WorkflowApprovalSubmission(
                    approvals={tool_call_id: ToolDenied(message="Too dangerous")},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission,
                )

                await wf_handle.result()

                # Verify approval status is REJECTED
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    approval = await svc.get_approval_by_session_and_tool(
                        session_id=session_id, tool_call_id=tool_call_id
                    )
                    assert approval is not None
                    assert isinstance(approval.decision, dict)
                    assert approval.status == ApprovalStatus.REJECTED
                    assert approval.decision["message"] == "Too dangerous"
        finally:
            if wf_handle:
                try:
                    await wf_handle.cancel()
                except Exception:
                    pass

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_approval_with_override_args(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
    ):
        """Verify that approval with override_args modifies tool call."""
        queue = TEST_AGENT_QUEUE
        session_id = test_agent_session_a.id

        executed_args: list[dict] = []

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            if call_count == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id="call_override_123",
                            name="core__http_request",
                            input={"url": "https://original.com", "method": "GET"},
                        )
                    ],
                )
            return AgentExecutorResult(success=True)

        def tool_result_callback(
            input: ExecuteApprovedToolsInput,
        ) -> ExecuteApprovedToolsResult:
            for tool in input.approved_tools:
                executed_args.append(tool.args)

            return ExecuteApprovedToolsResult(
                results=[
                    ToolExecutionResult(
                        tool_call_id=tool.tool_call_id,
                        tool_name=tool.tool_name,
                        result={"status": "executed"},
                        is_error=False,
                    )
                    for tool in input.approved_tools
                ],
                success=True,
            )

        activities = create_activities_with_mock_executor(
            mock_executor, tool_result_callback
        )

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Make request with override",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    workflow_args,
                    id=f"test-approval-override-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for approval
                tool_call_id = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc.list_approvals_for_session(session_id)
                        if approvals and approvals[0].status == ApprovalStatus.PENDING:
                            tool_call_id = approvals[0].tool_call_id
                            break

                assert tool_call_id is not None

                # Approve with override args
                submission = WorkflowApprovalSubmission(
                    approvals={
                        tool_call_id: ToolApproved(
                            override_args={"url": "https://safe.com"}
                        )
                    },
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission,
                )

                result = await wf_handle.result()
                assert result.session_id == session_id

                # Verify override was applied
                assert len(executed_args) == 1
                assert executed_args[0]["url"] == "https://safe.com"
                assert executed_args[0]["method"] == "GET"  # Original preserved

                # Verify override was recorded in decision
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    approval = await svc.get_approval_by_session_and_tool(
                        session_id=session_id, tool_call_id=tool_call_id
                    )
                    assert approval is not None
                    assert isinstance(approval.decision, dict)
                    assert approval.decision["kind"] == "tool-approved"
                    assert (
                        approval.decision["override_args"]["url"] == "https://safe.com"
                    )
        finally:
            if wf_handle:
                try:
                    await wf_handle.cancel()
                except Exception:
                    pass

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_multiple_approvals_in_single_turn(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
    ):
        """Verify handling of multiple tool approvals in a single turn."""
        queue = TEST_AGENT_QUEUE
        session_id = test_agent_session_a.id

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            if call_count == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id="call_multi_1",
                            name="core__http_request",
                            input={"url": "https://api1.com", "method": "GET"},
                        ),
                        ToolCallContent(
                            id="call_multi_2",
                            name="core__http_request",
                            input={"url": "https://api2.com", "method": "GET"},
                        ),
                    ],
                )
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Make multiple requests",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    workflow_args,
                    id=f"test-approval-multi-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for multiple approvals
                approvals = []
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc.list_approvals_for_session(session_id)
                        if len(approvals) >= 2:
                            all_pending = all(
                                a.status == ApprovalStatus.PENDING for a in approvals
                            )
                            if all_pending:
                                break

                assert len(approvals) == 2, (
                    f"Expected 2 approvals, got {len(approvals)}"
                )

                # Approve all
                submission = WorkflowApprovalSubmission(
                    approvals={a.tool_call_id: True for a in approvals},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission,
                )

                result = await wf_handle.result()
                assert result.session_id == session_id

                # Verify all approvals are APPROVED
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    updated = await svc.list_approvals_for_session(session_id)
                    assert len(updated) == 2
                    for approval in updated:
                        assert approval.status == ApprovalStatus.APPROVED
        finally:
            if wf_handle:
                try:
                    await wf_handle.cancel()
                except Exception:
                    pass


class TestApprovalConcurrency:
    """Tests for concurrent approval scenarios."""

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_multiple_sessions_awaiting_approval(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
        test_agent_session_b,
    ):
        """Verify multiple sessions can await approval independently."""
        queue = TEST_AGENT_QUEUE
        session_a = test_agent_session_a.id
        session_b = test_agent_session_b.id

        call_counts: dict[uuid.UUID, int] = {}

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            session_call = call_counts.get(input.session_id, 0)
            call_counts[input.session_id] = session_call + 1

            if session_call == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id=f"call_concurrent_{input.session_id}",
                            name="core__http_request",
                            input={"url": "https://example.com", "method": "GET"},
                        )
                    ],
                )
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        wf_args_a = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_a,
                user_prompt="Session A request",
                config=agent_config_with_approvals,
            ),
        )

        wf_args_b = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_b,
                user_prompt="Session B request",
                config=agent_config_with_approvals,
            ),
        )

        wf_handle_a = None
        wf_handle_b = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                # Start both workflows
                wf_handle_a = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    wf_args_a,
                    id=f"test-concurrent-a-{session_a}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                wf_handle_b = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    wf_args_b,
                    id=f"test-concurrent-b-{session_b}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Wait for both to have pending approvals
                approval_a = None
                approval_b = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals_a = await svc.list_approvals_for_session(session_a)
                        approvals_b = await svc.list_approvals_for_session(session_b)

                        if (
                            approvals_a
                            and approvals_a[0].status == ApprovalStatus.PENDING
                        ):
                            approval_a = approvals_a[0]
                        if (
                            approvals_b
                            and approvals_b[0].status == ApprovalStatus.PENDING
                        ):
                            approval_b = approvals_b[0]

                        if approval_a and approval_b:
                            break

                assert approval_a is not None, "Session A approval not found"
                assert approval_b is not None, "Session B approval not found"

                # Approve A only first
                submission_a = WorkflowApprovalSubmission(
                    approvals={approval_a.tool_call_id: True},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle_a.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission_a,
                )

                # A should complete
                result_a = await wf_handle_a.result()
                assert result_a.session_id == session_a

                # B should still be waiting
                desc_b = await wf_handle_b.describe()
                assert desc_b.status is not None
                assert desc_b.status.name == "RUNNING"

                # Now approve B
                submission_b = WorkflowApprovalSubmission(
                    approvals={approval_b.tool_call_id: True},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle_b.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission_b,
                )

                result_b = await wf_handle_b.result()
                assert result_b.session_id == session_b
        finally:
            for handle in [wf_handle_a, wf_handle_b]:
                if handle:
                    try:
                        await handle.cancel()
                    except Exception:
                        pass

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_approval_isolation_between_workspaces(
        self,
        role_workspace_agent_a: Role,
        role_workspace_agent_b: Role,
        temporal_client: Client,
        agent_worker_factory,
        agent_config_with_approvals: AgentConfig,
        test_agent_session_a,
        test_agent_session_workspace_b,
    ):
        """Verify approvals in one workspace don't affect another."""
        queue = TEST_AGENT_QUEUE
        session_a = test_agent_session_a.id
        session_b = test_agent_session_workspace_b.id

        call_counts: dict[uuid.UUID, int] = {}

        def mock_executor(
            input: AgentExecutorInput, call_count: int
        ) -> AgentExecutorResult:
            session_call = call_counts.get(input.session_id, 0)
            call_counts[input.session_id] = session_call + 1

            if session_call == 0:
                return AgentExecutorResult(
                    success=True,
                    approval_requested=True,
                    approval_items=[
                        ToolCallContent(
                            id=f"call_isolate_{input.session_id}",
                            name="core__http_request",
                            input={"url": "https://example.com", "method": "GET"},
                        )
                    ],
                )
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        wf_args_a = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_a,
                user_prompt="Workspace A request",
                config=agent_config_with_approvals,
            ),
        )

        wf_args_b = AgentWorkflowArgs(
            role=role_workspace_agent_b,
            agent_args=RunAgentArgs(
                session_id=session_b,
                user_prompt="Workspace B request",
                config=agent_config_with_approvals,
            ),
        )

        # Different workspace IDs
        assert (
            role_workspace_agent_a.workspace_id != role_workspace_agent_b.workspace_id
        )

        wf_handle_a = None
        wf_handle_b = None
        try:
            async with agent_worker_factory(
                temporal_client, task_queue=queue, custom_activities=activities
            ):
                wf_handle_a = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    wf_args_a,
                    id=f"test-isolate-a-{session_a}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                wf_handle_b = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    wf_args_b,
                    id=f"test-isolate-b-{session_b}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

                # Get approvals for each workspace
                approval_a = None
                approval_b = None
                async with ApprovalService.with_session(
                    role=role_workspace_agent_a
                ) as svc_a:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc_a.list_approvals_for_session(session_a)
                        if approvals and approvals[0].status == ApprovalStatus.PENDING:
                            approval_a = approvals[0]
                            break

                async with ApprovalService.with_session(
                    role=role_workspace_agent_b
                ) as svc_b:
                    for _ in range(50):
                        await asyncio.sleep(0.1)
                        approvals = await svc_b.list_approvals_for_session(session_b)
                        if approvals and approvals[0].status == ApprovalStatus.PENDING:
                            approval_b = approvals[0]
                            break

                assert approval_a is not None
                assert approval_b is not None

                # Approve both
                submission_a = WorkflowApprovalSubmission(
                    approvals={approval_a.tool_call_id: True},
                    approved_by=role_workspace_agent_a.user_id,
                )
                await wf_handle_a.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission_a,
                )

                submission_b = WorkflowApprovalSubmission(
                    approvals={approval_b.tool_call_id: True},
                    approved_by=role_workspace_agent_b.user_id,
                )
                await wf_handle_b.execute_update(
                    DurableAgentWorkflow.set_approvals,
                    submission_b,
                )

                result_a, result_b = await asyncio.gather(
                    wf_handle_a.result(),
                    wf_handle_b.result(),
                )

                # Both should complete independently
                assert result_a.session_id == session_a
                assert result_b.session_id == session_b
        finally:
            for handle in [wf_handle_a, wf_handle_b]:
                if handle:
                    try:
                        await handle.cancel()
                    except Exception:
                        pass
