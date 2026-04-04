"""Tests for DurableAgentWorkflow with approval handling.

These tests verify the end-to-end workflow behavior including:
- Agent execution loop
- Approval request handling
- Workflow updates for approval submission
- Integration with ApprovalManager
"""

import asyncio
import os
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

import orjson
import pytest

pytestmark = [pytest.mark.temporal, pytest.mark.usefixtures("db")]

from pydantic_ai.tools import ToolApproved, ToolDenied
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from tracecat_ee.agent.activities import BuildToolDefsArgs, BuildToolDefsResult
from tracecat_ee.agent.approvals.service import (
    ApprovalManager,
    ApprovalMap,
    ApprovalService,
)
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
)

from tracecat import config
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    ToolExecutionResult,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    LoadSessionInput,
    LoadSessionResult,
    ReconcileToolResultsInput,
    ReconcileToolResultsResult,
    create_session_activity,
    load_session_activity,
    reconcile_tool_results_activity,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import AgentSessionHistory, User
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.dsl.schemas import RunActionInput
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.tiers import defaults as tier_defaults


@pytest.fixture(autouse=True)
def enable_agent_approvals_entitlement(monkeypatch):
    """Enable agent approvals entitlement for all tests in this module."""
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"agent_addons": True}),
    )


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
            resume_source_session_id=None,
        )

    return mock_load_session_activity


def create_mock_build_tool_definitions_activity(
    tool_definitions: dict[str, MCPToolDefinition] | None = None,
) -> Callable[..., Any]:
    """Create a mock build_tool_definitions activity."""
    if tool_definitions is None:
        tool_definitions = {
            "core.http_request": MCPToolDefinition(
                name="core__http_request",
                description="Make HTTP requests",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "method": {"type": "string"},
                    },
                    "required": ["url", "method"],
                },
            )
        }

    # Create a mock registry lock for the tools
    registry_lock = RegistryLock(
        origins={"tracecat_registry": "test-version"},
        actions=dict.fromkeys(tool_definitions.keys(), "tracecat_registry"),
    )

    @activity.defn(name="build_tool_definitions")
    async def mock_build_tool_definitions(
        args: BuildToolDefsArgs,
    ) -> BuildToolDefsResult:
        return BuildToolDefsResult(
            tool_definitions=tool_definitions,
            registry_lock=registry_lock,
        )

    return mock_build_tool_definitions


def create_mock_run_agent_activity(
    response_callback: Callable[[int, AgentExecutorInput], AgentExecutorResult],
) -> Callable[..., Any]:
    """Create a mock run_agent_activity with stateful responses.

    Args:
        response_callback: Function that takes (call_count, input) and returns AgentExecutorResult.
                          call_count starts at 0 and increments with each call.
    """
    call_count = 0

    @activity.defn(name="run_agent_activity")
    async def mock_run_agent_activity(
        input: AgentExecutorInput,
    ) -> AgentExecutorResult:
        nonlocal call_count
        activity.heartbeat("Mock agent running")
        result = response_callback(call_count, input)
        call_count += 1
        return result

    return mock_run_agent_activity


def create_mock_execute_action_activity(
    response_callback: Callable[[RunActionInput], InlineObject[dict[str, str]]]
    | None = None,
) -> Callable[..., Any]:
    """Create a mock executor activity for approved registry UDFs."""

    @activity.defn(name="execute_action_activity")
    async def mock_execute_action_activity(
        input: RunActionInput,
        role: Role,
    ) -> InlineObject[dict[str, str]]:
        del role
        if response_callback:
            return response_callback(input)
        return InlineObject(data={"status": "success"})

    return mock_execute_action_activity


def create_mock_reconcile_tool_results_activity() -> Callable[..., Any]:
    """Create a mock reconciliation activity for approval continuation."""

    @activity.defn(name="reconcile_tool_results_activity")
    async def mock_reconcile_tool_results_activity(
        input: ReconcileToolResultsInput,
    ) -> ReconcileToolResultsResult:
        results = [
            ToolExecutionResult(
                tool_call_id=pending.tool_call_id,
                tool_name=pending.tool_name,
                result={"status": "success"}
                if pending.stored_result is not None
                else pending.raw_result,
                is_error=pending.is_error,
            )
            for pending in input.pending_results
        ]
        return ReconcileToolResultsResult(results=results)

    return mock_reconcile_tool_results_activity


def create_activities_with_mock_executor(
    response_callback: Callable[[int, AgentExecutorInput], AgentExecutorResult],
    tool_exec_callback: Callable[[RunActionInput], InlineObject[dict[str, str]]]
    | None = None,
    tool_definitions: dict[str, MCPToolDefinition] | None = None,
) -> Sequence[Callable[..., Any]]:
    """Create a full activity list with mocked agent executor.

    Args:
        response_callback: Function for mock run_agent_activity.
        tool_exec_callback: Optional function for mock execute_action_activity.
        tool_definitions: Optional tool definitions for build_tool_definitions.

    Returns:
        List of activities including mocked activities and approval manager activities.
    """
    activities: list[Callable[..., Any]] = [
        create_mock_create_session_activity(),
        create_mock_load_session_activity(),
        create_mock_build_tool_definitions_activity(tool_definitions),
        create_mock_run_agent_activity(response_callback),
        create_mock_execute_action_activity(tool_exec_callback),
        create_mock_reconcile_tool_results_activity(),
        *ApprovalManager.get_activities(),
    ]
    return activities


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_session_id() -> uuid.UUID:
    """Return a fixed session ID for testing."""
    return uuid.uuid4()


@pytest.fixture
async def test_user(svc_role: Role) -> User:
    """Create a test user for approval tests.

    This user is committed to the actual database (not in a test transaction)
    so that Temporal activities can see it.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.db.engine import get_async_engine

    user_id = svc_role.user_id if svc_role.user_id else uuid.uuid4()

    engine = get_async_engine()
    async with AsyncSession(engine) as session:
        # Check if user already exists
        result = await session.execute(select(User).where(User.id == user_id))  # pyright: ignore[reportArgumentType]
        existing_user = result.scalars().first()

        if existing_user:
            return existing_user

        # Create new user
        user = User(
            id=user_id,
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="test_password",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            last_login_at=None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
def agent_config() -> AgentConfig:
    """Create a basic agent configuration without approvals."""
    return AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        namespaces=None,
        actions=["core.http_request"],
        tool_approvals=None,
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
def run_agent_args(
    mock_session_id: uuid.UUID, agent_config: AgentConfig
) -> RunAgentArgs:
    """Create RunAgentArgs for testing."""
    return RunAgentArgs(
        session_id=mock_session_id,
        user_prompt="Test prompt",
        config=agent_config,
    )


@pytest.fixture
def agent_workflow_args(
    svc_role: Role, run_agent_args: RunAgentArgs
) -> AgentWorkflowArgs:
    """Create AgentWorkflowArgs for testing."""
    return AgentWorkflowArgs(
        role=svc_role,
        agent_args=run_agent_args,
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )


@pytest.fixture
async def agent_worker_factory(threadpool, monkeypatch: pytest.MonkeyPatch):
    """Factory to create workers configured for agent workflows."""

    def create_agent_worker(
        client: Client,
        *,
        task_queue: str | None = None,
        custom_activities: Sequence[Callable[..., Any]] | None = None,
    ) -> Worker:
        """Create a worker for agent workflows with required activities.

        Args:
            client: Temporal client
            task_queue: Task queue name
            custom_activities: List of custom activities to register.
        """
        if custom_activities is None:
            # Default: create activities that complete immediately
            def simple_response(
                call_count: int, input: AgentExecutorInput
            ) -> AgentExecutorResult:
                return AgentExecutorResult(success=True)

            custom_activities = create_activities_with_mock_executor(simple_response)

        queue_name = task_queue or os.environ["TEMPORAL__CLUSTER_QUEUE"]
        monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", queue_name)
        monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", queue_name)

        return Worker(
            client=client,
            task_queue=queue_name,
            activities=custom_activities,
            workflows=[DurableAgentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activity_executor=threadpool,
        )

    yield create_agent_worker


# =============================================================================
# Tests: Basic Workflow Execution
# =============================================================================


@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_simple_execution(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    agent_workflow_args: AgentWorkflowArgs,
    mock_session_id: uuid.UUID,
) -> None:
    """Test basic agent workflow execution without approvals.

    The mock agent executor returns success immediately, so the workflow
    completes without requiring any approvals.
    """
    # Use unique task queue to avoid conflicts with dev worker
    queue = f"test-agent-queue-{mock_session_id}"

    # Create mock that returns success immediately (no approval needed)
    def mock_executor(
        call_count: int, input: AgentExecutorInput
    ) -> AgentExecutorResult:
        return AgentExecutorResult(success=True, approval_requested=False)

    activities = create_activities_with_mock_executor(mock_executor)

    async with agent_worker_factory(
        temporal_client, task_queue=queue, custom_activities=activities
    ):
        wf_handle = await temporal_client.start_workflow(
            DurableAgentWorkflow.run,
            agent_workflow_args,
            id=AgentWorkflowID(mock_session_id),
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=30),
        )

        result = await wf_handle.result()
        assert result.session_id == mock_session_id


@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_uses_agent_config_model_settings(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
) -> None:
    """Ensure DurableAgentWorkflow passes AgentConfig settings to executor."""
    queue = f"test-agent-queue-{mock_session_id}"

    seen_config: AgentConfig | None = None

    def mock_executor(
        call_count: int, input: AgentExecutorInput
    ) -> AgentExecutorResult:
        nonlocal seen_config
        if seen_config is None:
            seen_config = input.config
        return AgentExecutorResult(success=True)

    activities = create_activities_with_mock_executor(mock_executor)

    raw_settings: dict[str, object] = {"temperature": 0.42}
    agent_config_with_settings = AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        namespaces=None,
        actions=["core.http_request"],
        tool_approvals=None,
        model_settings=raw_settings,
    )

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Test prompt with settings",
            config=agent_config_with_settings,
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
            id=AgentWorkflowID(mock_session_id),
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=30),
        )

        result = await wf_handle.result()
        assert result.session_id == mock_session_id

    assert seen_config is not None
    assert seen_config.model_settings == raw_settings


@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_routes_approved_tools_to_executor_and_reconciles_history(
    svc_role: Role,
    temporal_client: Client,
    mock_session_id: uuid.UUID,
    agent_config_with_approvals: AgentConfig,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
    threadpool,
) -> None:
    del test_user
    agent_queue = f"test-agent-queue-{mock_session_id}"
    agent_executor_queue = f"test-agent-executor-queue-{mock_session_id}"
    executor_queue = f"test-executor-queue-{mock_session_id}"

    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", agent_queue)
    monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", agent_executor_queue)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", executor_queue)

    approval_request_recorded = asyncio.Event()
    resumed_after_approval = asyncio.Event()
    agent_executor_task_queues: list[str] = []
    executor_task_queues: list[str] = []
    captured_run_inputs: list[RunActionInput] = []
    captured_executor_roles: list[Role] = []

    class _FakeStream:
        async def append(self, event: Any) -> None:
            del event

    async def fake_agent_stream_new(
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> _FakeStream:
        del session_id, workspace_id
        return _FakeStream()

    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentStream.new",
        fake_agent_stream_new,
    )

    @activity.defn(name="build_tool_definitions")
    async def mock_build_tool_definitions(
        args: BuildToolDefsArgs,
    ) -> BuildToolDefsResult:
        del args
        return BuildToolDefsResult(
            tool_definitions={
                "core.http_request": MCPToolDefinition(
                    name="core__http_request",
                    description="HTTP request",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "method": {"type": "string"},
                        },
                        "required": ["url", "method"],
                        "additionalProperties": False,
                    },
                )
            },
            registry_lock=RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={"core.http_request": "tracecat_registry"},
            ),
        )

    run_agent_call_count = 0

    @activity.defn(name="run_agent_activity")
    async def mock_run_agent_activity(
        input: AgentExecutorInput,
    ) -> AgentExecutorResult:
        nonlocal run_agent_call_count
        agent_executor_task_queues.append(activity.info().task_queue)
        activity.heartbeat("Mock agent running")

        if run_agent_call_count == 0:
            assistant_uuid = str(uuid.uuid4())
            async with AgentSessionService.with_session(role=input.role) as service:
                session = await service.get_session(input.session_id)
                assert session is not None
                session.sdk_session_id = "sdk-session"
                service.session.add(session)
                service.session.add(
                    AgentSessionHistory(
                        session_id=input.session_id,
                        workspace_id=input.workspace_id,
                        kind="chat-message",
                        content={
                            "uuid": assistant_uuid,
                            "sessionId": "sdk-session",
                            "type": "assistant",
                            "timestamp": "2026-03-18T00:00:00Z",
                            "cwd": "/home/agent",
                            "version": "2.0.72",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "call_123",
                                        "name": "core__http_request",
                                        "input": {
                                            "url": "https://example.com",
                                            "method": "GET",
                                        },
                                    }
                                ],
                            },
                        },
                    )
                )
                service.session.add(
                    AgentSessionHistory(
                        session_id=input.session_id,
                        workspace_id=input.workspace_id,
                        kind="chat-message",
                        content={
                            "uuid": str(uuid.uuid4()),
                            "parentUuid": assistant_uuid,
                            "sessionId": "sdk-session",
                            "type": "user",
                            "timestamp": "2026-03-18T00:00:01Z",
                            "cwd": "/home/agent",
                            "version": "2.0.72",
                            "userType": "external",
                            "gitBranch": "",
                            "isSidechain": False,
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "call_123",
                                        "content": "interrupted",
                                        "is_error": True,
                                    }
                                ],
                            },
                        },
                    )
                )
                await service.session.commit()

            run_agent_call_count += 1
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

        assert input.is_approval_continuation is True
        assert input.sdk_session_id == "sdk-session"
        assert input.resume_source_session_id == input.session_id
        assert input.resume_source_session_id is not None

        async with AgentSessionService.with_session(role=input.role) as service:
            session_history = await service.materialize_session_history(
                input.resume_source_session_id
            )
        assert session_history is not None

        session_lines = [
            orjson.loads(line) for line in session_history.splitlines() if line.strip()
        ]
        tool_result_blocks = [
            block
            for entry in session_lines
            for block in entry.get("message", {}).get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert any(
            block.get("tool_use_id") == "call_123" and block.get("is_error") is False
            for block in tool_result_blocks
        )
        resumed_after_approval.set()
        run_agent_call_count += 1
        return AgentExecutorResult(
            success=True,
            approval_requested=False,
            output={"status": "done"},
        )

    @activity.defn(name="record_approval_requests")
    async def mock_record_approval_requests(input: Any) -> None:
        del input
        approval_request_recorded.set()

    @activity.defn(name="apply_approval_decisions")
    async def mock_apply_approval_decisions(input: Any) -> None:
        del input

    @activity.defn(name="execute_action_activity")
    async def mock_execute_action_activity(
        input: RunActionInput,
        role: Role,
    ) -> InlineObject[dict[str, str]]:
        executor_task_queues.append(activity.info().task_queue)
        captured_run_inputs.append(input)
        captured_executor_roles.append(role)
        return InlineObject(
            data={
                "status": "success",
                "executor_queue": activity.info().task_queue,
            }
        )

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make a test HTTP request",
            config=agent_config_with_approvals,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    workflow_worker = Worker(
        client=temporal_client,
        task_queue=agent_queue,
        activities=[
            create_session_activity,
            load_session_activity,
            reconcile_tool_results_activity,
            mock_build_tool_definitions,
            mock_record_approval_requests,
            mock_apply_approval_decisions,
        ],
        workflows=[DurableAgentWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activity_executor=threadpool,
    )
    agent_executor_worker = Worker(
        client=temporal_client,
        task_queue=agent_executor_queue,
        activities=[mock_run_agent_activity],
        workflows=[],
        activity_executor=threadpool,
    )
    executor_worker = Worker(
        client=temporal_client,
        task_queue=executor_queue,
        activities=[mock_execute_action_activity],
        workflows=[],
        activity_executor=threadpool,
    )

    async with workflow_worker, agent_executor_worker, executor_worker:
        wf_handle = await temporal_client.start_workflow(
            DurableAgentWorkflow.run,
            workflow_args,
            id=AgentWorkflowID(mock_session_id),
            task_queue=agent_queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=60),
        )

        await asyncio.wait_for(approval_request_recorded.wait(), timeout=10)
        await wf_handle.execute_update(
            DurableAgentWorkflow.set_approvals,
            WorkflowApprovalSubmission(
                approvals={"call_123": True},
                approved_by=svc_role.user_id,
            ),
        )

        result = await wf_handle.result()

    assert result.session_id == mock_session_id
    assert agent_executor_task_queues == [
        agent_executor_queue,
        agent_executor_queue,
    ]
    assert executor_task_queues == [executor_queue]
    assert resumed_after_approval.is_set()
    assert len(captured_run_inputs) == 1
    assert len(captured_executor_roles) == 1
    assert captured_run_inputs[0].task.action == "core__http_request"
    assert captured_executor_roles[0].type == "service"
    assert captured_executor_roles[0].service_id == "tracecat-mcp"
    assert captured_executor_roles[0].workspace_id == svc_role.workspace_id
    assert captured_executor_roles[0].organization_id == svc_role.organization_id
    assert captured_executor_roles[0].user_id == svc_role.user_id
    assert captured_executor_roles[0].scopes == SERVICE_PRINCIPAL_SCOPES["tracecat-mcp"]

    async with AgentSessionService.with_session(role=svc_role) as service:
        history = await service.get_session_history(mock_session_id)

    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert any(
        block.get("tool_use_id") == "call_123" and block.get("is_error") is False
        for block in tool_result_blocks
    )
    assert not any(
        block.get("tool_use_id") == "call_123" and block.get("is_error") is True
        for block in tool_result_blocks
    )
    inserted_block = next(
        block for block in tool_result_blocks if block.get("tool_use_id") == "call_123"
    )
    assert orjson.loads(inserted_block["content"]) == {
        "status": "success",
        "executor_queue": executor_queue,
    }


@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_does_not_retry_approved_tool_failures(
    svc_role: Role,
    temporal_client: Client,
    mock_session_id: uuid.UUID,
    agent_config_with_approvals: AgentConfig,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
    threadpool,
) -> None:
    del test_user
    agent_queue = f"test-agent-queue-{mock_session_id}"
    agent_executor_queue = f"test-agent-executor-queue-{mock_session_id}"
    executor_queue = f"test-executor-queue-{mock_session_id}"

    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", agent_queue)
    monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", agent_executor_queue)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", executor_queue)

    approval_request_recorded = asyncio.Event()
    resumed_after_approval = asyncio.Event()
    executor_attempts = 0
    run_agent_call_count = 0

    class _FakeStream:
        async def append(self, event: Any) -> None:
            del event

    async def fake_agent_stream_new(
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> _FakeStream:
        del session_id, workspace_id
        return _FakeStream()

    monkeypatch.setattr(
        "tracecat.agent.session.activities.AgentStream.new",
        fake_agent_stream_new,
    )

    @activity.defn(name="build_tool_definitions")
    async def mock_build_tool_definitions(
        args: BuildToolDefsArgs,
    ) -> BuildToolDefsResult:
        del args
        return BuildToolDefsResult(
            tool_definitions={
                "core.http_request": MCPToolDefinition(
                    name="core__http_request",
                    description="HTTP request",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "method": {"type": "string"},
                        },
                        "required": ["url", "method"],
                        "additionalProperties": False,
                    },
                )
            },
            registry_lock=RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={"core.http_request": "tracecat_registry"},
            ),
        )

    @activity.defn(name="run_agent_activity")
    async def mock_run_agent_activity(
        input: AgentExecutorInput,
    ) -> AgentExecutorResult:
        nonlocal run_agent_call_count
        activity.heartbeat("Mock agent running")

        if run_agent_call_count == 0:
            assistant_uuid = str(uuid.uuid4())
            async with AgentSessionService.with_session(role=input.role) as service:
                session = await service.get_session(input.session_id)
                assert session is not None
                session.sdk_session_id = "sdk-session"
                service.session.add(session)
                service.session.add(
                    AgentSessionHistory(
                        session_id=input.session_id,
                        workspace_id=input.workspace_id,
                        kind="chat-message",
                        content={
                            "uuid": assistant_uuid,
                            "sessionId": "sdk-session",
                            "type": "assistant",
                            "timestamp": "2026-03-18T00:00:00Z",
                            "cwd": "/home/agent",
                            "version": "2.0.72",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "call_123",
                                        "name": "core__http_request",
                                        "input": {
                                            "url": "https://example.com",
                                            "method": "GET",
                                        },
                                    }
                                ],
                            },
                        },
                    )
                )
                service.session.add(
                    AgentSessionHistory(
                        session_id=input.session_id,
                        workspace_id=input.workspace_id,
                        kind="chat-message",
                        content={
                            "uuid": str(uuid.uuid4()),
                            "parentUuid": assistant_uuid,
                            "sessionId": "sdk-session",
                            "type": "user",
                            "timestamp": "2026-03-18T00:00:01Z",
                            "cwd": "/home/agent",
                            "version": "2.0.72",
                            "userType": "external",
                            "gitBranch": "",
                            "isSidechain": False,
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "call_123",
                                        "content": "interrupted",
                                        "is_error": True,
                                    }
                                ],
                            },
                        },
                    )
                )
                await service.session.commit()

            run_agent_call_count += 1
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

        assert input.is_approval_continuation is True
        assert input.sdk_session_id == "sdk-session"
        assert input.resume_source_session_id == input.session_id
        assert input.resume_source_session_id is not None

        async with AgentSessionService.with_session(role=input.role) as service:
            session_history = await service.materialize_session_history(
                input.resume_source_session_id
            )
        assert session_history is not None

        session_lines = [
            orjson.loads(line) for line in session_history.splitlines() if line.strip()
        ]
        tool_result_blocks = [
            block
            for entry in session_lines
            for block in entry.get("message", {}).get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert any(
            block.get("tool_use_id") == "call_123" and block.get("is_error") is True
            for block in tool_result_blocks
        )
        resumed_after_approval.set()
        run_agent_call_count += 1
        return AgentExecutorResult(
            success=True,
            approval_requested=False,
            output={"status": "done"},
        )

    @activity.defn(name="record_approval_requests")
    async def mock_record_approval_requests(input: Any) -> None:
        del input
        approval_request_recorded.set()

    @activity.defn(name="apply_approval_decisions")
    async def mock_apply_approval_decisions(input: Any) -> None:
        del input

    @activity.defn(name="execute_action_activity")
    async def mock_execute_action_activity(
        input: RunActionInput,
        role: Role,
    ) -> InlineObject[dict[str, str]]:
        del input, role
        nonlocal executor_attempts
        executor_attempts += 1
        if executor_attempts == 1:
            raise ApplicationError("transient tool failure")
        return InlineObject(data={"status": "success"})

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make a test HTTP request",
            config=agent_config_with_approvals,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    workflow_worker = Worker(
        client=temporal_client,
        task_queue=agent_queue,
        activities=[
            create_session_activity,
            load_session_activity,
            reconcile_tool_results_activity,
            mock_build_tool_definitions,
            mock_record_approval_requests,
            mock_apply_approval_decisions,
        ],
        workflows=[DurableAgentWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activity_executor=threadpool,
    )
    agent_executor_worker = Worker(
        client=temporal_client,
        task_queue=agent_executor_queue,
        activities=[mock_run_agent_activity],
        workflows=[],
        activity_executor=threadpool,
    )
    executor_worker = Worker(
        client=temporal_client,
        task_queue=executor_queue,
        activities=[mock_execute_action_activity],
        workflows=[],
        activity_executor=threadpool,
    )

    async with workflow_worker, agent_executor_worker, executor_worker:
        wf_handle = await temporal_client.start_workflow(
            DurableAgentWorkflow.run,
            workflow_args,
            id=AgentWorkflowID(mock_session_id),
            task_queue=agent_queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=60),
        )

        await asyncio.wait_for(approval_request_recorded.wait(), timeout=10)
        await wf_handle.execute_update(
            DurableAgentWorkflow.set_approvals,
            WorkflowApprovalSubmission(
                approvals={"call_123": True},
                approved_by=svc_role.user_id,
            ),
        )

        result = await wf_handle.result()

    assert result.session_id == mock_session_id
    assert executor_attempts == 1
    assert resumed_after_approval.is_set()

    async with AgentSessionService.with_session(role=svc_role) as service:
        history = await service.get_session_history(mock_session_id)

    tool_result_blocks = [
        block
        for entry in history
        for block in entry.content.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    inserted_block = next(
        block for block in tool_result_blocks if block.get("tool_use_id") == "call_123"
    )
    assert inserted_block["is_error"] is True
    assert "Tool execution failed:" in inserted_block["content"]


# =============================================================================
# Tests: Approval Handling
# =============================================================================


@pytest.mark.skip(
    reason="TODO: Approval persistence via Temporal activity needs database session fix"
)
@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_with_single_approval(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
    agent_config_with_approvals: AgentConfig,
    test_user: User,
) -> None:
    """Test agent workflow that requires single approval.

    Workflow should:
    1. Start agent execution
    2. Agent requests tool approval
    3. Workflow waits for approval
    4. User submits approval via workflow update
    5. Workflow continues and completes
    """
    queue = f"test-agent-queue-{mock_session_id}"

    def mock_executor(
        call_count: int, input: AgentExecutorInput
    ) -> AgentExecutorResult:
        if call_count == 0:
            # First call: request approval for a tool
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
        else:
            # Second call: after approval, complete successfully
            return AgentExecutorResult(success=True, approval_requested=False)

    activities = create_activities_with_mock_executor(mock_executor)

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make a test HTTP request",
            config=agent_config_with_approvals,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    wf_handle = None
    try:
        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=AgentWorkflowID(mock_session_id),
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for approval to be pending
            tool_call_id = None
            async with ApprovalService.with_session(role=svc_role) as svc:
                for _ in range(50):  # Max 5 seconds
                    await asyncio.sleep(0.1)
                    approvals = await svc.list_approvals_for_session(mock_session_id)
                    if approvals:
                        assert len(approvals) == 1
                        approval = approvals[0]
                        await svc.session.refresh(approval)
                        if approval.status == ApprovalStatus.PENDING:
                            tool_call_id = approval.tool_call_id
                            break

            assert tool_call_id is not None, "No pending approval found"

            # Submit approval via workflow update
            submission = WorkflowApprovalSubmission(
                approvals={tool_call_id: True},
                approved_by=svc_role.user_id,
            )
            await wf_handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                submission,
            )

            # Wait for workflow to complete
            result = await wf_handle.result()
            assert result.session_id == mock_session_id

            # Verify approval was applied
            async with ApprovalService.with_session(role=svc_role) as svc:
                approval = await svc.get_approval_by_session_and_tool(
                    session_id=mock_session_id,
                    tool_call_id=tool_call_id,
                )
                assert approval is not None
                assert approval.status == ApprovalStatus.APPROVED
                assert approval.approved_by == svc_role.user_id
    except Exception:
        if wf_handle:
            try:
                await wf_handle.terminate(reason="Test failed")
            except Exception:
                pass
        raise


@pytest.mark.skip(
    reason="TODO: Approval persistence via Temporal activity needs database session fix"
)
@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_with_multiple_approvals(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
    agent_config_with_approvals: AgentConfig,
    test_user: User,
) -> None:
    """Test agent workflow with multiple simultaneous approvals.

    Tests the case where the agent requests approval for multiple tools
    in a single turn and all must be approved/rejected together.
    """
    queue = f"test-agent-queue-{mock_session_id}"

    def mock_executor(
        call_count: int, input: AgentExecutorInput
    ) -> AgentExecutorResult:
        if call_count == 0:
            # First call: request multiple tools
            return AgentExecutorResult(
                success=True,
                approval_requested=True,
                approval_items=[
                    ToolCallContent(
                        id="call_123",
                        name="core__http_request",
                        input={"url": "https://api1.example.com", "method": "GET"},
                    ),
                    ToolCallContent(
                        id="call_456",
                        name="core__http_request",
                        input={"url": "https://api2.example.com", "method": "GET"},
                    ),
                ],
            )
        else:
            return AgentExecutorResult(success=True)

    activities = create_activities_with_mock_executor(mock_executor)

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make multiple test HTTP requests",
            config=agent_config_with_approvals,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    wf_handle = None
    try:
        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=AgentWorkflowID(mock_session_id),
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for multiple approvals to be pending
            approvals = []
            async with ApprovalService.with_session(role=svc_role) as svc:
                for _ in range(50):  # Max 5 seconds
                    await asyncio.sleep(0.1)
                    approvals = await svc.list_approvals_for_session(mock_session_id)
                    if len(approvals) >= 2:
                        all_pending = all(
                            a.status == ApprovalStatus.PENDING for a in approvals
                        )
                        if all_pending:
                            break

            assert len(approvals) == 2, f"Expected 2 approvals, got {len(approvals)}"

            # Submit approvals for all tools
            approval_map: ApprovalMap = {a.tool_call_id: True for a in approvals}
            submission = WorkflowApprovalSubmission(
                approvals=approval_map,
                approved_by=svc_role.user_id,
            )
            await wf_handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                submission,
            )

            result = await wf_handle.result()
            assert result.session_id == mock_session_id

            # Verify all approvals were applied
            async with ApprovalService.with_session(role=svc_role) as svc:
                updated_approvals = await svc.list_approvals_for_session(
                    mock_session_id
                )
                assert len(updated_approvals) == 2
                for approval in updated_approvals:
                    assert approval.status == ApprovalStatus.APPROVED
                    assert approval.approved_by == svc_role.user_id
    except Exception:
        if wf_handle:
            try:
                await wf_handle.terminate(reason="Test failed")
            except Exception:
                pass
        raise


@pytest.mark.skip(
    reason="TODO: Approval persistence via Temporal activity needs database session fix"
)
@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_approval_with_override_args(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
    agent_config_with_approvals: AgentConfig,
    test_user: User,
) -> None:
    """Test approving a tool call with modified arguments.

    Uses ToolApproved with override_args to modify the tool call parameters.
    """
    queue = f"test-agent-queue-{mock_session_id}"

    def mock_executor(
        call_count: int, input: AgentExecutorInput
    ) -> AgentExecutorResult:
        if call_count == 0:
            return AgentExecutorResult(
                success=True,
                approval_requested=True,
                approval_items=[
                    ToolCallContent(
                        id="call_789",
                        name="core__http_request",
                        input={"url": "https://original.example.com", "method": "GET"},
                    )
                ],
            )
        else:
            return AgentExecutorResult(success=True)

    activities = create_activities_with_mock_executor(mock_executor)

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make HTTP request with override",
            config=agent_config_with_approvals,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    wf_handle = None
    try:
        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=AgentWorkflowID(mock_session_id),
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for approval
            tool_call_id = None
            async with ApprovalService.with_session(role=svc_role) as svc:
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    approvals = await svc.list_approvals_for_session(mock_session_id)
                    if approvals:
                        approval = approvals[0]
                        await svc.session.refresh(approval)
                        if approval.status == ApprovalStatus.PENDING:
                            tool_call_id = approval.tool_call_id
                            break

            assert tool_call_id is not None, "No pending approval found"

            # Approve with override args
            submission = WorkflowApprovalSubmission(
                approvals={
                    tool_call_id: ToolApproved(
                        override_args={"url": "https://modified.example.com"}
                    )
                },
                approved_by=svc_role.user_id,
            )
            await wf_handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                submission,
            )

            result = await wf_handle.result()
            assert result.session_id == mock_session_id

            # Verify decision includes override args
            async with ApprovalService.with_session(role=svc_role) as svc:
                approval = await svc.get_approval_by_session_and_tool(
                    session_id=mock_session_id,
                    tool_call_id=tool_call_id,
                )
                assert approval is not None
                assert approval.status == ApprovalStatus.APPROVED
                assert isinstance(approval.decision, dict)
                assert approval.decision["kind"] == "tool-approved"
                assert "override_args" in approval.decision
                assert (
                    approval.decision["override_args"]["url"]
                    == "https://modified.example.com"
                )
    except Exception:
        if wf_handle:
            try:
                await wf_handle.terminate(reason="Test failed")
            except Exception:
                pass
        raise


@pytest.mark.skip(
    reason="TODO: Approval persistence via Temporal activity needs database session fix"
)
@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_mixed_approvals_and_rejections(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
    agent_config_with_approvals: AgentConfig,
    test_user: User,
) -> None:
    """Test workflow with some tools approved and others rejected.

    Verifies that:
    - Approved tools execute
    - Rejected tools don't execute but are recorded
    - Agent receives both results and continues
    """
    queue = f"test-agent-queue-{mock_session_id}"

    def mock_executor(
        call_count: int, input: AgentExecutorInput
    ) -> AgentExecutorResult:
        if call_count == 0:
            return AgentExecutorResult(
                success=True,
                approval_requested=True,
                approval_items=[
                    ToolCallContent(
                        id="call_safe",
                        name="core__http_request",
                        input={"url": "https://safe.example.com", "method": "GET"},
                    ),
                    ToolCallContent(
                        id="call_risky",
                        name="core__http_request",
                        input={"url": "https://risky.example.com", "method": "DELETE"},
                    ),
                ],
            )
        else:
            return AgentExecutorResult(success=True)

    activities = create_activities_with_mock_executor(mock_executor)

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make mixed requests",
            config=agent_config_with_approvals,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    wf_handle = None
    try:
        async with agent_worker_factory(
            temporal_client, task_queue=queue, custom_activities=activities
        ):
            wf_handle = await temporal_client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=AgentWorkflowID(mock_session_id),
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for approvals
            approvals = []
            async with ApprovalService.with_session(role=svc_role) as svc:
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    approvals = await svc.list_approvals_for_session(mock_session_id)
                    if len(approvals) >= 2:
                        all_pending = all(
                            a.status == ApprovalStatus.PENDING for a in approvals
                        )
                        if all_pending:
                            break

            assert len(approvals) == 2, f"Expected 2 approvals, got {len(approvals)}"

            # Approve first, reject second
            submission = WorkflowApprovalSubmission(
                approvals={
                    approvals[0].tool_call_id: True,
                    approvals[1].tool_call_id: ToolDenied(message="Too risky"),
                },
                approved_by=svc_role.user_id,
            )
            await wf_handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                submission,
            )

            result = await wf_handle.result()
            assert result.session_id == mock_session_id

            # Verify mixed statuses
            async with ApprovalService.with_session(role=svc_role) as svc:
                updated_approvals = await svc.list_approvals_for_session(
                    mock_session_id
                )
                statuses = {a.tool_call_id: a.status for a in updated_approvals}
                assert ApprovalStatus.APPROVED in statuses.values()
                assert ApprovalStatus.REJECTED in statuses.values()

                # Verify rejection message
                rejected = [
                    a for a in updated_approvals if a.status == ApprovalStatus.REJECTED
                ]
                assert len(rejected) == 1
                assert isinstance(rejected[0].decision, dict)
                assert rejected[0].decision["message"] == "Too risky"
    except Exception:
        if wf_handle:
            try:
                await wf_handle.terminate(reason="Test failed")
            except Exception:
                pass
        raise


# =============================================================================
# Tests: Workflow State Management
# =============================================================================


@pytest.mark.anyio
class TestWorkflowValidation:
    """Test approval validation logic in the workflow."""

    async def test_validation_rejects_missing_approvals(
        self,
        agent_workflow_args: AgentWorkflowArgs,
    ) -> None:
        """Test that workflow validator rejects submissions missing required approvals.

        This tests the validator in isolation without running a full workflow.
        """
        # This would require instantiating the workflow in a test context
        # which is complex with Temporal. Instead, we test the ApprovalManager
        # validation directly in test_approvals_manager.py
        pass

    async def test_validation_rejects_unexpected_approvals(
        self,
        agent_workflow_args: AgentWorkflowArgs,
    ) -> None:
        """Test that workflow validator rejects extra unexpected approvals."""
        pass


@pytest.mark.anyio
class TestAgentWorkflowStateManagement:
    """Test workflow state management and turn tracking."""

    async def test_workflow_tracks_turns(self) -> None:
        """Test that workflow properly increments turn counter."""
        # This would be tested in full integration tests
        pass

    async def test_workflow_handles_max_turns(self) -> None:
        """Test that workflow respects max turns limit (when implemented)."""
        # Feature not yet implemented but should be tested when added
        pass
