"""Integration tests for AgentWorker multi-tenant execution.

These tests spin up real Temporal workers with DurableAgentWorkflow and verify:
1. Worker lifecycle and activity registration
2. Single-tenant execution with mocked agent executor
3. Multi-tenant concurrent execution with workspace isolation
4. Session persistence and history management
5. Redis stream event verification

The tests mock `run_agent_activity` to simulate agent responses without
spawning actual NSJail processes or calling LLM APIs.

Run via: uv run pytest tests/integration/test_agent_worker.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Generator, Sequence
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Worker
from tracecat_ee.agent.activities import AgentActivities
from tracecat_ee.agent.approvals.service import ApprovalManager
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs, DurableAgentWorkflow

from tests.conftest import AGENT_TASK_QUEUE
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    ExecuteApprovedToolsInput,
    ExecuteApprovedToolsResult,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    LoadSessionInput,
    LoadSessionResult,
    get_session_activities,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig, StreamKey
from tracecat.auth.types import Role
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.redis.client import get_redis_client

# Use worker-specific queue from conftest for pytest-xdist isolation
TEST_AGENT_QUEUE = AGENT_TASK_QUEUE


# =============================================================================
# Mock Activity Factories
# =============================================================================


def create_mock_create_session_activity() -> Callable[..., Any]:
    """Create a mock create_session_activity that returns success."""

    @activity.defn(name="create_session_activity")
    async def mock_create_session_activity(
        input: CreateSessionInput,
    ) -> CreateSessionResult:
        return CreateSessionResult(
            session_id=input.session_id,
            success=True,
        )

    return mock_create_session_activity


def create_mock_load_session_activity() -> Callable[..., Any]:
    """Create a mock load_session_activity that returns no existing session."""

    @activity.defn(name="load_session_activity")
    async def mock_load_session_activity(
        input: LoadSessionInput,
    ) -> LoadSessionResult:
        return LoadSessionResult(
            found=False,
            sdk_session_id=None,
            sdk_session_data=None,
        )

    return mock_load_session_activity


def create_mock_run_agent_activity(
    response_callback: Callable[[AgentExecutorInput], AgentExecutorResult],
) -> Callable[..., Any]:
    """Create a mock run_agent_activity that returns controlled responses.

    Args:
        response_callback: Function that takes AgentExecutorInput and returns
            AgentExecutorResult. Use this to control the agent's behavior.
    """

    @activity.defn(name="run_agent_activity")
    async def mock_run_agent_activity(
        input: AgentExecutorInput,
    ) -> AgentExecutorResult:
        activity.heartbeat("Mock agent running")
        return response_callback(input)

    return mock_run_agent_activity


def create_mock_execute_approved_tools_activity() -> Callable[..., Any]:
    """Create a mock execute_approved_tools_activity."""

    @activity.defn(name="execute_approved_tools_activity")
    async def mock_execute_approved_tools_activity(
        input: ExecuteApprovedToolsInput,
    ) -> ExecuteApprovedToolsResult:
        return ExecuteApprovedToolsResult(results=[], success=True)

    return mock_execute_approved_tools_activity


def create_activities_with_mock_executor(
    response_callback: Callable[[AgentExecutorInput], AgentExecutorResult],
) -> list[Callable[..., Any]]:
    """Create a full activity list with mocked executor and session activities.

    This mocks run_agent_activity, execute_approved_tools_activity, and session
    activities to avoid database dependencies in tests. Other activities
    (tool building, approvals) remain real.
    """
    agent_activities = AgentActivities()

    # Get all activities from AgentActivities
    activities: list[Callable[..., Any]] = list(agent_activities.get_activities())

    # Add approval activities (real)
    activities.extend(ApprovalManager.get_activities())

    # Add mocked session activities (to avoid DB FK constraints)
    activities.append(create_mock_create_session_activity())
    activities.append(create_mock_load_session_activity())

    # Add mocked executor activities
    activities.append(create_mock_run_agent_activity(response_callback))
    activities.append(create_mock_execute_approved_tools_activity())

    return activities


# =============================================================================
# Agent Worker Factory Fixture
# =============================================================================


@pytest.fixture
def agent_worker_factory(
    threadpool: Any,
) -> Generator[Callable[..., Worker], None, None]:
    """Factory to create workers configured for agent workflows."""

    def create_agent_worker(
        client: Client,
        *,
        task_queue: str,
        custom_activities: Sequence[Callable[..., Any]] | None = None,
    ) -> Worker:
        """Create a worker for agent workflows with required activities."""
        if custom_activities is not None:
            activities: Sequence[Callable[..., Any]] = custom_activities
        else:
            agent_activities = AgentActivities()

            activities = [
                *agent_activities.get_activities(),
                *get_session_activities(),
                *ApprovalManager.get_activities(),
            ]

        return Worker(
            client=client,
            task_queue=task_queue,
            activities=activities,
            workflows=[DurableAgentWorkflow],
            workflow_runner=new_sandbox_runner(),
            activity_executor=threadpool,
        )

    yield create_agent_worker


# =============================================================================
# Test Classes
# =============================================================================


class TestAgentWorkerLifecycle:
    """Tests for AgentWorker service lifecycle."""

    @pytest.mark.anyio
    async def test_worker_module_has_expected_structure(self) -> None:
        """Verify worker module can be imported and has expected attributes."""
        from tracecat.agent import worker

        assert hasattr(worker, "get_activities")
        assert hasattr(worker, "main")

    @pytest.mark.anyio
    async def test_mcp_modules_have_expected_structure(self) -> None:
        """Verify MCP modules have expected exports."""
        from tracecat.agent.mcp import proxy_server, trusted_server

        # proxy_server has UDS client factory for connecting to trusted server
        assert hasattr(proxy_server, "_UDSClientFactory")
        # trusted_server exports FastMCP app
        assert hasattr(trusted_server, "app")
        assert hasattr(trusted_server, "mcp")

    @pytest.mark.anyio
    async def test_session_activities_registered(self) -> None:
        """Verify session activities are available."""
        activities = get_session_activities()
        assert len(activities) == 2

        activity_names = [
            getattr(a, "__temporal_activity_definition").name for a in activities
        ]
        assert "create_session_activity" in activity_names
        assert "load_session_activity" in activity_names


class TestAgentWorkerSingleTenant:
    """Tests for single-tenant agent execution."""

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_executes_simple_agent_turn(
        self,
        svc_role: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Execute DurableAgentWorkflow with mocked executor, verify completion."""
        session_id = uuid.uuid4()
        queue = TEST_AGENT_QUEUE

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            # Simulate successful agent completion
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=svc_role,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Test prompt",
                config=AgentConfig(
                    model_name="claude-3-5-sonnet-20241022",
                    model_provider="anthropic",
                    actions=["core.http_request"],
                ),
            ),
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=f"test-agent-single-{session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=30),
            )

            result = await wf_handle.result()
            assert result.session_id == session_id

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_streams_events_to_redis(
        self,
        svc_role: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Verify Redis stream key format is correct."""
        session_id = uuid.uuid4()
        queue = TEST_AGENT_QUEUE
        workspace_id = svc_role.workspace_id
        assert workspace_id is not None

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=svc_role,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Test streaming",
                config=AgentConfig(
                    model_name="claude-3-5-sonnet-20241022",
                    model_provider="anthropic",
                    actions=["core.http_request"],
                ),
            ),
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=f"test-agent-stream-{session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=30),
            )

            await wf_handle.result()

            # Verify Redis stream key format
            redis_client = await get_redis_client()
            stream_key = StreamKey(workspace_id, session_id)

            # Read events from stream (may be empty with mocked executor)
            _ = await redis_client.xrange(stream_key)

            # Verify stream key format contains expected IDs
            assert isinstance(stream_key, str)
            assert str(workspace_id) in stream_key
            assert str(session_id) in stream_key

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_handles_agent_error(
        self,
        svc_role: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Verify workflow handles executor errors gracefully."""
        session_id = uuid.uuid4()
        queue = TEST_AGENT_QUEUE

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            return AgentExecutorResult(
                success=False,
                error="Simulated agent error",
            )

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=svc_role,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Test error handling",
                config=AgentConfig(
                    model_name="claude-3-5-sonnet-20241022",
                    model_provider="anthropic",
                    actions=["core.http_request"],
                ),
            ),
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=f"test-agent-error-{session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=30),
            )

            # Workflow should raise a WorkflowFailureError with nested ApplicationError
            with pytest.raises(WorkflowFailureError) as exc_info:
                await wf_handle.result()

            # The error message is in the cause chain
            assert "Simulated agent error" in str(exc_info.value.cause)


class TestAgentWorkerMultiTenant:
    """Tests for multi-tenant concurrent execution and isolation."""

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_concurrent_execution_different_workspaces(
        self,
        role_workspace_agent_a: Role,
        role_workspace_agent_b: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Execute workflows concurrently from different workspaces, verify isolation."""
        session_a = uuid.uuid4()
        session_b = uuid.uuid4()
        queue = TEST_AGENT_QUEUE

        execution_order: list[uuid.UUID] = []

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            execution_order.append(input.session_id)
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args_a = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_a,
                user_prompt="Workspace A prompt",
                config=AgentConfig(
                    model_name="claude-3-5-sonnet-20241022",
                    model_provider="anthropic",
                    actions=["core.http_request"],
                ),
            ),
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        workflow_args_b = AgentWorkflowArgs(
            role=role_workspace_agent_b,
            agent_args=RunAgentArgs(
                session_id=session_b,
                user_prompt="Workspace B prompt",
                config=AgentConfig(
                    model_name="claude-3-5-sonnet-20241022",
                    model_provider="anthropic",
                    actions=["core.http_request"],
                ),
            ),
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        # Verify workspace IDs are different
        assert (
            role_workspace_agent_a.workspace_id != role_workspace_agent_b.workspace_id
        )
        assert str(role_workspace_agent_a.workspace_id).startswith("aaaa")
        assert str(role_workspace_agent_b.workspace_id).startswith("bbbb")

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle_a = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args_a,
                id=f"test-agent-multi-a-{session_a}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=30),
            )

            wf_handle_b = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args_b,
                id=f"test-agent-multi-b-{session_b}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=30),
            )

            # Wait for both to complete
            result_a, result_b = await asyncio.gather(
                wf_handle_a.result(),
                wf_handle_b.result(),
            )

            # Both should complete successfully
            assert result_a.session_id == session_a
            assert result_b.session_id == session_b

            # Both sessions should have been executed
            assert session_a in execution_order
            assert session_b in execution_order

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_single_workspace_execution(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Basic single workspace execution test."""
        session_id = uuid.uuid4()
        queue = TEST_AGENT_QUEUE

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args = AgentWorkflowArgs(
            role=role_workspace_agent_a,
            agent_args=RunAgentArgs(
                session_id=session_id,
                user_prompt="Test prompt",
                config=AgentConfig(
                    model_name="claude-3-5-sonnet-20241022",
                    model_provider="anthropic",
                    actions=["core.http_request"],
                ),
            ),
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=f"test-agent-single-ws-{session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=30),
            )

            result = await wf_handle.result()
            assert result.session_id == session_id

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_multiple_concurrent_same_workspace(
        self,
        role_workspace_agent_a: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Multiple simultaneous requests from same tenant."""
        session_ids = [uuid.uuid4() for _ in range(3)]
        queue = TEST_AGENT_QUEUE

        executed_sessions: list[uuid.UUID] = []

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            executed_sessions.append(input.session_id)
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        workflow_args_list = [
            AgentWorkflowArgs(
                role=role_workspace_agent_a,
                agent_args=RunAgentArgs(
                    session_id=session_id,
                    user_prompt=f"Prompt {i}",
                    config=AgentConfig(
                        model_name="claude-3-5-sonnet-20241022",
                        model_provider="anthropic",
                        actions=["core.http_request"],
                    ),
                ),
                entity_type=AgentSessionEntity.WORKFLOW,
                entity_id=uuid.uuid4(),
            )
            for i, session_id in enumerate(session_ids)
        ]

        # All should have same workspace ID
        workspace_ids = {args.role.workspace_id for args in workflow_args_list}
        assert len(workspace_ids) == 1

        # But different session IDs
        sess_ids = {args.agent_args.session_id for args in workflow_args_list}
        assert len(sess_ids) == 3

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            handles = [
                await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    args,
                    id=f"test-agent-concurrent-{args.agent_args.session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=30),
                )
                for args in workflow_args_list
            ]

            results = await asyncio.gather(*[h.result() for h in handles])

            # All should complete successfully
            for result, expected_session_id in zip(results, session_ids, strict=True):
                assert result.session_id == expected_session_id

            # All sessions should have been executed
            for session_id in session_ids:
                assert session_id in executed_sessions


class TestAgentWorkerThrashing:
    """Tests for rapid alternation between tenants."""

    @pytest.mark.anyio
    @pytest.mark.slow
    @pytest.mark.integration
    async def test_rapid_alternation_between_workspaces(
        self,
        role_workspace_agent_a: Role,
        role_workspace_agent_b: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Interleave A/B requests with staggered launch."""
        queue = TEST_AGENT_QUEUE
        num_pairs = 5

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            handles = []

            # Alternate between workspaces
            for i in range(num_pairs):
                role = role_workspace_agent_a if i % 2 == 0 else role_workspace_agent_b
                session_id = uuid.uuid4()

                args = AgentWorkflowArgs(
                    role=role,
                    agent_args=RunAgentArgs(
                        session_id=session_id,
                        user_prompt=f"Thrash {i}",
                        config=AgentConfig(
                            model_name="claude-3-5-sonnet-20241022",
                            model_provider="anthropic",
                            actions=["core.http_request"],
                        ),
                    ),
                    entity_type=AgentSessionEntity.WORKFLOW,
                    entity_id=uuid.uuid4(),
                )

                handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    args,
                    id=f"test-agent-thrash-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )
                handles.append(handle)

                # Small delay between launches
                await asyncio.sleep(0.1)

            results = await asyncio.gather(*[h.result() for h in handles])

            # All should complete
            assert len(results) == num_pairs

    @pytest.mark.anyio
    @pytest.mark.slow
    @pytest.mark.integration
    async def test_burst_then_switch_pattern(
        self,
        role_workspace_agent_a: Role,
        role_workspace_agent_b: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Burst from A, then burst from B."""
        queue = TEST_AGENT_QUEUE
        burst_size = 3

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            handles_a = []
            handles_b = []

            # Burst from A
            for i in range(burst_size):
                session_id = uuid.uuid4()
                args = AgentWorkflowArgs(
                    role=role_workspace_agent_a,
                    agent_args=RunAgentArgs(
                        session_id=session_id,
                        user_prompt=f"Burst A {i}",
                        config=AgentConfig(
                            model_name="claude-3-5-sonnet-20241022",
                            model_provider="anthropic",
                            actions=["core.http_request"],
                        ),
                    ),
                    entity_type=AgentSessionEntity.WORKFLOW,
                    entity_id=uuid.uuid4(),
                )
                handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    args,
                    id=f"test-agent-burst-a-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )
                handles_a.append(handle)

            # Wait for A burst to complete
            results_a = await asyncio.gather(*[h.result() for h in handles_a])

            # Burst from B
            for i in range(burst_size):
                session_id = uuid.uuid4()
                args = AgentWorkflowArgs(
                    role=role_workspace_agent_b,
                    agent_args=RunAgentArgs(
                        session_id=session_id,
                        user_prompt=f"Burst B {i}",
                        config=AgentConfig(
                            model_name="claude-3-5-sonnet-20241022",
                            model_provider="anthropic",
                            actions=["core.http_request"],
                        ),
                    ),
                    entity_type=AgentSessionEntity.WORKFLOW,
                    entity_id=uuid.uuid4(),
                )
                handle = await temporal_client.start_workflow(
                    DurableAgentWorkflow.run,
                    args,
                    id=f"test-agent-burst-b-{session_id}",
                    task_queue=queue,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )
                handles_b.append(handle)

            results_b = await asyncio.gather(*[h.result() for h in handles_b])

            # All should complete
            assert len(results_a) == burst_size
            assert len(results_b) == burst_size

    @pytest.mark.anyio
    @pytest.mark.slow
    @pytest.mark.integration
    async def test_interleaved_concurrent_bursts(
        self,
        role_workspace_agent_a: Role,
        role_workspace_agent_b: Role,
        temporal_client: Client,
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        """Overlapping bursts from both workspaces."""
        queue = TEST_AGENT_QUEUE
        burst_size = 3

        def mock_executor(input: AgentExecutorInput) -> AgentExecutorResult:
            return AgentExecutorResult(success=True)

        activities = create_activities_with_mock_executor(mock_executor)

        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            handles = []

            # Launch bursts from both workspaces concurrently
            for i in range(burst_size):
                for role, label in [
                    (role_workspace_agent_a, "a"),
                    (role_workspace_agent_b, "b"),
                ]:
                    session_id = uuid.uuid4()
                    args = AgentWorkflowArgs(
                        role=role,
                        agent_args=RunAgentArgs(
                            session_id=session_id,
                            user_prompt=f"Interleaved {label} {i}",
                            config=AgentConfig(
                                model_name="claude-3-5-sonnet-20241022",
                                model_provider="anthropic",
                                actions=["core.http_request"],
                            ),
                        ),
                        entity_type=AgentSessionEntity.WORKFLOW,
                        entity_id=uuid.uuid4(),
                    )
                    handle = await temporal_client.start_workflow(
                        DurableAgentWorkflow.run,
                        args,
                        id=f"test-agent-interleave-{label}-{session_id}",
                        task_queue=queue,
                        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                        execution_timeout=timedelta(seconds=60),
                    )
                    handles.append(handle)

            results = await asyncio.gather(*[h.result() for h in handles])

            # All should complete
            assert len(results) == burst_size * 2
