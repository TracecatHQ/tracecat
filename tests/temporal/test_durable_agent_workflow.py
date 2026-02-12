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

import pytest

pytestmark = [pytest.mark.temporal, pytest.mark.usefixtures("db")]

from pydantic_ai.tools import ToolApproved, ToolDenied
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from tracecat_ee.agent.activities import BuildToolDefsArgs, BuildToolDefsResult
from tracecat_ee.agent.approvals.service import (
    ApprovalManager,
    ApprovalMap,
    ApprovalService,
)
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
)

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    ExecuteApprovedToolsInput,
    ExecuteApprovedToolsResult,
    ToolExecutionResult,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    LoadSessionInput,
    LoadSessionResult,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import User
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.registry.lock.types import RegistryLock
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
            sdk_session_data=None,
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


def create_mock_execute_approved_tools_activity(
    response_callback: Callable[[ExecuteApprovedToolsInput], ExecuteApprovedToolsResult]
    | None = None,
) -> Callable[..., Any]:
    """Create a mock execute_approved_tools_activity."""

    @activity.defn(name="execute_approved_tools_activity")
    async def mock_execute_approved_tools_activity(
        input: ExecuteApprovedToolsInput,
    ) -> ExecuteApprovedToolsResult:
        if response_callback:
            return response_callback(input)
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
        # Add denial results for rejected tools
        for denied in input.denied_tools:
            results.append(
                ToolExecutionResult(
                    tool_call_id=denied.tool_call_id,
                    tool_name=denied.tool_name,
                    result=f"Tool denied: {denied.reason}",
                    is_error=True,
                )
            )
        return ExecuteApprovedToolsResult(results=results, success=True)

    return mock_execute_approved_tools_activity


def create_activities_with_mock_executor(
    response_callback: Callable[[int, AgentExecutorInput], AgentExecutorResult],
    tool_exec_callback: Callable[
        [ExecuteApprovedToolsInput], ExecuteApprovedToolsResult
    ]
    | None = None,
    tool_definitions: dict[str, MCPToolDefinition] | None = None,
) -> Sequence[Callable[..., Any]]:
    """Create a full activity list with mocked agent executor.

    Args:
        response_callback: Function for mock run_agent_activity.
        tool_exec_callback: Optional function for mock execute_approved_tools_activity.
        tool_definitions: Optional tool definitions for build_tool_definitions.

    Returns:
        List of activities including mocked activities and approval manager activities.
    """
    activities: list[Callable[..., Any]] = [
        create_mock_create_session_activity(),
        create_mock_load_session_activity(),
        create_mock_build_tool_definitions_activity(tool_definitions),
        create_mock_run_agent_activity(response_callback),
        create_mock_execute_approved_tools_activity(tool_exec_callback),
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
async def agent_worker_factory(threadpool):
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

        return Worker(
            client=client,
            task_queue=task_queue or os.environ["TEMPORAL__CLUSTER_QUEUE"],
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
            id=f"test-agent-{mock_session_id}",
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
            id=f"test-agent-settings-{mock_session_id}",
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            execution_timeout=timedelta(seconds=30),
        )

        result = await wf_handle.result()
        assert result.session_id == mock_session_id

    assert seen_config is not None
    assert seen_config.model_settings == raw_settings


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
                id=f"test-agent-approval-{mock_session_id}",
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
                id=f"test-agent-multi-approval-{mock_session_id}",
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
                id=f"test-agent-override-{mock_session_id}",
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
                id=f"test-agent-mixed-{mock_session_id}",
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
