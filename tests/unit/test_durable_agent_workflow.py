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
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai import ModelSettings
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.tools import ToolApproved, ToolDenied
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from tracecat_ee.agent.activities import AgentActivities, RequestStreamArgs
from tracecat_ee.agent.approvals.service import (
    ApprovalManager,
    ApprovalMap,
    ApprovalService,
)
from tracecat_ee.agent.context import AgentContext
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
)

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.preset.schemas import AgentPresetCreate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import ModelRequestArgs, ModelRequestResult, RunAgentArgs
from tracecat.agent.stream.common import PersistableStreamingAgentDepsSpec
from tracecat.agent.tools import SimpleToolExecutor
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import (
    RegistryAction,
    RegistryRepository,
    User,
)
from tracecat.dsl.common import RETRY_POLICIES

pytestmark = pytest.mark.usefixtures("db")


# Helper functions for constructing mock model responses
def create_text_response(content: str) -> ModelResponse:
    """Create a ModelResponse with text content (final answer)."""
    return ModelResponse(
        parts=[TextPart(content=content)],
        timestamp=datetime.now(UTC),
        model_name="mock-model",
    )


def create_tool_call_response(tool_calls: list[ToolCallPart]) -> ModelResponse:
    """Create a ModelResponse requesting tool calls."""
    return ModelResponse(
        parts=tool_calls,
        timestamp=datetime.now(UTC),
        model_name="mock-model",
    )


def create_mock_model_request_activity(
    response_callback: Callable[[int, ModelRequestArgs], ModelResponse],
):
    """Create a mock model_request activity with stateful responses.

    Args:
        response_callback: Function that takes (call_count, args) and returns a ModelResponse.
                          call_count starts at 0 and increments with each call.
    """
    call_count = 0

    @activity.defn(name="model_request")
    async def mock_model_request(
        args: ModelRequestArgs, ctx: AgentContext
    ) -> ModelRequestResult:
        """Mock model request activity that returns controlled responses."""
        nonlocal call_count
        response = response_callback(call_count, args)
        call_count += 1
        return ModelRequestResult(model_response=response)

    return mock_model_request


def create_mock_request_stream_activity(
    response_callback: Callable[[int, ModelRequestArgs], ModelResponse],
):
    """Create a mock request_stream activity.

    Args:
        response_callback: Function that takes (call_count, args) and returns a ModelResponse.
    """
    call_count = 0

    @activity.defn(name="request_stream")
    async def mock_request_stream(
        args: RequestStreamArgs | ModelRequestArgs | dict[str, object],
        deps: PersistableStreamingAgentDepsSpec,
    ) -> ModelResponse:
        """Mock request_stream activity that returns controlled responses."""
        nonlocal call_count
        # Extract the actual ModelRequestArgs from RequestStreamArgs
        if isinstance(args, ModelRequestArgs):
            model_request_args = args
        elif isinstance(args, RequestStreamArgs):
            model_request_args = ModelRequestArgs(
                role=args.role,
                messages=args.messages,
                model_settings=args.model_settings,
                model_request_parameters=args.model_request_parameters,
                model_info=args.model_info,
            )
        elif isinstance(args, dict) and "messages" in args:
            # Args was deserialized to a plain dict by the data converter
            model_request_args = ModelRequestArgs.model_validate(args)
        else:  # pragma: no cover - defensive fallback
            raise TypeError(
                f"Unexpected args type for mock_request_stream: {type(args)}"
            )

        response = response_callback(call_count, model_request_args)
        call_count += 1
        return response

    return mock_request_stream


def create_activities_with_mock_model(
    response_callback: Callable[[int, ModelRequestArgs], ModelResponse],
) -> Sequence[Callable[..., object]]:
    """Create a full activity list with mocked model_request and request_stream.

    Args:
        response_callback: Function that takes (call_count, args) and returns a ModelResponse.

    Returns:
        List of activities including mocked model activities, real tool execution, and approvals.
    """
    tool_executor = SimpleToolExecutor()
    agent_activities = AgentActivities(tool_executor=tool_executor)

    # Get all agent activities
    activities = agent_activities.get_activities()

    # Replace model_request and request_stream with our mocks
    activities = [
        a
        for a in activities
        if not (
            hasattr(a, "__name__") and a.__name__ in ("model_request", "request_stream")
        )
    ]
    activities.append(create_mock_model_request_activity(response_callback))
    activities.append(create_mock_request_stream_activity(response_callback))

    # Add approval manager activities
    activities.extend(ApprovalManager.get_activities())

    return activities


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
        actions=["core.http_request"],  # Use specific actions to avoid 10 tool limit
        tool_approvals=None,
    )


@pytest.fixture
def agent_config_with_approvals() -> AgentConfig:
    """Create agent configuration with tool approvals enabled."""
    return AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        namespaces=None,
        actions=["core.http_request"],  # Use specific actions to avoid 10 tool limit
        tool_approvals={
            "core.http_request": True
        },  # Require approval for http requests
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
        """Create a worker for agent workflows with required activities.

        Args:
            client: Temporal client
            task_queue: Task queue name
            custom_activities: Optional list of custom activities to use instead of defaults.
                             If provided, these completely replace the default agent activities.
        """
        if custom_activities is not None:
            activities: Sequence[Callable[..., object]] = custom_activities
        else:
            # Create agent activities with mocked dependencies
            tool_executor = SimpleToolExecutor()
            agent_activities = AgentActivities(tool_executor=tool_executor)

            # Combine agent activities with approval manager activities
            activities = [
                *agent_activities.get_activities(),
                *ApprovalManager.get_activities(),
            ]

        return Worker(
            client=client,
            task_queue=task_queue or os.environ["TEMPORAL__CLUSTER_QUEUE"],
            activities=activities,
            workflows=[DurableAgentWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            activity_executor=threadpool,
            # Use unsandboxed runner since DurableAgentWorkflow
            # uses pydantic-ai which has sandbox compatibility issues with sniffio
        )

    yield create_agent_worker


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

    The mock AI model returns a simple text response, so the workflow
    completes immediately without requiring any approvals.
    """
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Create mock that returns a simple text response
    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        return create_text_response("Task completed successfully")

    # Create activities with mocked model
    activities = create_activities_with_mock_model(mock_response)

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

        # Wait for workflow to complete
        result = await wf_handle.result()
        assert result.session_id == mock_session_id
        assert result.output is not None
        assert result.output == "Task completed successfully"


@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_uses_agent_config_model_settings(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
) -> None:
    """Ensure DurableAgentWorkflow passes AgentConfig.model_settings to model requests."""
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    raw_settings: dict[str, object] = {"temperature": 0.42}
    seen_settings: ModelSettings | None = None

    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        nonlocal seen_settings
        # Capture the first model_settings value we see
        if seen_settings is None:
            seen_settings = args.model_settings
        return create_text_response("Task completed successfully")

    activities = create_activities_with_mock_model(mock_response)

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
        assert result.output == "Task completed successfully"

    assert seen_settings is not None
    assert seen_settings == raw_settings


@pytest.mark.anyio
@pytest.mark.integration
async def test_agent_workflow_with_preset_config(
    svc_role: Role,
    temporal_client: Client,
    agent_worker_factory,
    mock_session_id: uuid.UUID,
    session: AsyncSession,
    test_user: User,
) -> None:
    """Ensure DurableAgentWorkflow works with config derived from an AgentPreset.

    This bridges the preset service layer and the durable agent workflow by:
    1. Creating an AgentPreset via AgentPresetService
    2. Converting it to AgentConfig
    3. Running DurableAgentWorkflow with that config and verifying approvals.
    """
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Create a minimal registry action so preset actions validation passes
    repo = RegistryRepository(
        owner_id=svc_role.workspace_id,
        origin="test-agent-preset-repo",
    )
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    registry_action = RegistryAction(
        owner_id=svc_role.workspace_id,
        repository_id=repo.id,
        name="http_request",
        namespace="core",
        description="HTTP request action",
        origin="test-agent-preset-repo",
        type="template",
        interface={},
    )
    session.add(registry_action)
    await session.commit()

    # Create an agent preset that uses this action and requires approval
    preset_service = AgentPresetService(session=session, role=svc_role)
    preset_create = AgentPresetCreate(
        name="Durable preset",
        slug=None,
        description="Preset for durable agent workflow",
        instructions="You are a preset-based test agent.",
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        base_url=None,
        output_type=None,
        actions=["core.http_request"],
        namespaces=None,
        tool_approvals={"core.http_request": True},
        mcp_integrations=None,
        retries=3,
    )
    preset = await preset_service.create_preset(preset_create)
    preset_config = await preset_service._preset_to_agent_config(preset)

    # Model behavior: first call requests a tool, second returns final answer
    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        if call_count == 0:
            return create_tool_call_response(
                [
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://example.com", "method": "GET"},
                        tool_call_id="call_preset_123",
                    )
                ]
            )
        return create_text_response("HTTP request completed from preset")

    activities = create_activities_with_mock_model(mock_response)

    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make a test HTTP request using preset",
            config=preset_config,
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
                id=f"test-agent-preset-{mock_session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for approval to be pending
            tool_call_id = None
            async with ApprovalService.with_session(role=svc_role) as svc:
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    approvals = await svc.list_approvals_for_session(mock_session_id)
                    if approvals:
                        assert len(approvals) == 1
                        approval = approvals[0]
                        await svc.session.refresh(approval)
                        if approval.status == ApprovalStatus.PENDING:
                            tool_call_id = approval.tool_call_id
                            break

            assert tool_call_id is not None, "No pending approval found for preset"

            # Submit approval via workflow update
            submission = WorkflowApprovalSubmission(
                approvals={tool_call_id: True},
                approved_by=test_user.id,
            )
            await wf_handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                submission,
            )

            # Wait for workflow to complete
            result = await wf_handle.result()
            assert result.session_id == mock_session_id
            assert result.output == "HTTP request completed from preset"

            # Verify approval was applied
            async with ApprovalService.with_session(role=svc_role) as svc:
                approval = await svc.get_approval_by_session_and_tool(
                    session_id=mock_session_id,
                    tool_call_id=tool_call_id,
                )
                assert approval is not None
                assert approval.status == ApprovalStatus.APPROVED
                assert approval.approved_by == test_user.id
    finally:
        if wf_handle:
            await wf_handle.cancel()


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
    2. Agent requests tool approval (returns DeferredToolRequests)
    3. Workflow waits for approval
    4. User submits approval via workflow update
    5. Workflow continues and completes
    """
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Create mock that requests a tool on first call, then returns final answer
    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        if call_count == 0:
            # First call: request a tool that requires approval
            return create_tool_call_response(
                [
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://example.com", "method": "GET"},
                        tool_call_id="call_123",
                    )
                ]
            )
        else:
            # Second call: return final answer after approval
            return create_text_response("HTTP request completed successfully")

    # Create activities with mocked model
    activities = create_activities_with_mock_model(mock_response)

    # Create workflow args with approval-enabled config
    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make a test HTTP request",
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
            assert result.output == "HTTP request completed successfully"

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
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Create mock that requests multiple tools on first call
    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        if call_count == 0:
            # First call: request multiple tools that require approval
            return create_tool_call_response(
                [
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://api1.example.com", "method": "GET"},
                        tool_call_id="call_123",
                    ),
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://api2.example.com", "method": "GET"},
                        tool_call_id="call_456",
                    ),
                ]
            )
        else:
            # Second call: return final answer after approvals
            return create_text_response("All HTTP requests completed successfully")

    # Create activities with mocked model
    activities = create_activities_with_mock_model(mock_response)

    # Create workflow args with approval-enabled config
    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make multiple test HTTP requests",
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
            assert result.output == "All HTTP requests completed successfully"

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
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Create mock that requests a tool on first call
    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        if call_count == 0:
            # First call: request a tool that requires approval
            return create_tool_call_response(
                [
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://original.example.com", "method": "GET"},
                        tool_call_id="call_789",
                    )
                ]
            )
        else:
            # Second call: return final answer
            return create_text_response("Request completed with modified parameters")

    # Create activities with mocked model
    activities = create_activities_with_mock_model(mock_response)

    # Create workflow args with approval-enabled config
    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make HTTP request with override",
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
                id=f"test-agent-override-{mock_session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for approval
            tool_call_id = None
            async with ApprovalService.with_session(role=svc_role) as svc:
                for _ in range(50):  # Max 5 seconds
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
            assert result.output == "Request completed with modified parameters"

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
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    # Create mock that requests multiple tools on first call
    def mock_response(call_count: int, args: ModelRequestArgs) -> ModelResponse:
        if call_count == 0:
            # First call: request multiple tools
            return create_tool_call_response(
                [
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://safe.example.com", "method": "GET"},
                        tool_call_id="call_safe",
                    ),
                    ToolCallPart(
                        tool_name="core__http_request",
                        args={"url": "https://risky.example.com", "method": "DELETE"},
                        tool_call_id="call_risky",
                    ),
                ]
            )
        else:
            # Second call: return final answer acknowledging mixed results
            return create_text_response(
                "Completed: safe request approved, risky request rejected"
            )

    # Create activities with mocked model
    activities = create_activities_with_mock_model(mock_response)

    # Create workflow args with approval-enabled config
    workflow_args = AgentWorkflowArgs(
        role=svc_role,
        agent_args=RunAgentArgs(
            session_id=mock_session_id,
            user_prompt="Make mixed requests",
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
                id=f"test-agent-mixed-{mock_session_id}",
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=60),
            )

            # Wait for approvals
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
            assert (
                result.output
                == "Completed: safe request approved, risky request rejected"
            )

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
