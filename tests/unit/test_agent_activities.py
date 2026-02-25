"""Tests for agent Temporal activities.

These tests cover:
- Session management activities (create, load)
- run_agent_activity (sandboxed/direct subprocess execution)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    run_agent_activity,
)
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    LoadSessionInput,
    LoadSessionResult,
    create_session_activity,
    get_session_activities,
    load_session_activity,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


@pytest.fixture
def mock_session_id() -> uuid.UUID:
    """Create a mock session ID for testing."""
    return uuid.uuid4()


@pytest.fixture
def mock_agent_config() -> AgentConfig:
    """Create a mock agent config for testing."""
    return AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
    )


class TestSessionActivities:
    """Tests for session-related activities registration."""

    def test_get_session_activities_returns_list(self):
        """Test that get_session_activities returns a list of activity functions."""
        activities = get_session_activities()
        assert isinstance(activities, list)
        assert len(activities) == 2

        # All returned items should have the temporal activity definition
        for activity in activities:
            assert hasattr(activity, "__temporal_activity_definition")

    def test_session_activities_names(self):
        """Test that all expected session activities are included."""
        activities = get_session_activities()
        activity_names = [
            getattr(a, "__temporal_activity_definition").name for a in activities
        ]
        assert "create_session_activity" in activity_names
        assert "load_session_activity" in activity_names


class TestCreateSessionActivity:
    """Tests for create_session_activity."""

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_creates_new_session(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test successful session creation."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            harness_type=HarnessType.CLAUDE_CODE,
        )

        # Set up the mock service
        mock_service = AsyncMock()
        mock_service.get_or_create_session.return_value = (MagicMock(), True)

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await create_session_activity(input)

        assert isinstance(result, CreateSessionResult)
        assert result.success is True
        assert result.session_id == mock_session_id
        assert result.error is None

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_idempotent_for_existing_session(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test that creating an existing session is idempotent."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )

        # Set up the mock service
        mock_service = AsyncMock()
        mock_service.get_or_create_session.return_value = (MagicMock(), False)

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await create_session_activity(input)

        assert result.success is True
        assert result.session_id == mock_session_id

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_sets_correct_harness_type(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test that harness_type is correctly passed."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            harness_type=HarnessType.CLAUDE_CODE,
        )

        # Set up the mock service
        mock_service = AsyncMock()
        mock_service.get_or_create_session.return_value = (MagicMock(), True)

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        await create_session_activity(input)

        # Verify the call was made with correct harness_type
        call_args = mock_service.get_or_create_session.call_args
        create_schema = call_args[0][0]
        assert create_schema.harness_type == "claude_code"

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_auto_titles_with_initial_prompt(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test auto-title attempt when an initial user prompt is provided."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            initial_user_prompt="Investigate login failures",
        )

        mock_agent_session = MagicMock()
        mock_service = AsyncMock()
        mock_service.get_or_create_session.return_value = (mock_agent_session, True)
        mock_service.auto_title_session_on_first_prompt = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await create_session_activity(input)

        assert result.success is True
        mock_service.auto_title_session_on_first_prompt.assert_awaited_once_with(
            mock_agent_session,
            "Investigate login failures",
        )

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_skips_auto_title_for_existing_session(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test initial prompt does not retrigger auto-title for existing sessions."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            initial_user_prompt="Investigate repeated login failures",
        )

        mock_agent_session = MagicMock()
        mock_service = AsyncMock()
        mock_service.get_or_create_session.return_value = (mock_agent_session, False)
        mock_service.auto_title_session_on_first_prompt = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await create_session_activity(input)

        assert result.success is True
        mock_service.auto_title_session_on_first_prompt.assert_not_awaited()


class TestLoadSessionActivity:
    """Tests for load_session_activity."""

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_returns_not_found_for_missing_session(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test that loading a non-existent session returns found=False."""
        input = LoadSessionInput(
            role=mock_role,
            session_id=mock_session_id,
        )

        # Set up the mock service
        mock_service = AsyncMock()
        mock_service.get_session.return_value = None

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await load_session_activity(input)

        assert isinstance(result, LoadSessionResult)
        assert result.found is False

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_loads_session_without_history(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test loading a session that has no history."""
        input = LoadSessionInput(
            role=mock_role,
            session_id=mock_session_id,
        )

        mock_agent_session = MagicMock()

        # Set up the mock service
        mock_service = AsyncMock()
        mock_service.get_session.return_value = mock_agent_session
        mock_service.load_session_history.return_value = None

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await load_session_activity(input)

        assert result.found is True
        assert result.sdk_session_id is None
        assert result.sdk_session_data is None

    @pytest.mark.anyio
    @patch("tracecat.agent.session.activities.AgentSessionService.with_session")
    async def test_loads_session_with_history(
        self, mock_with_session, mock_role: Role, mock_session_id: uuid.UUID
    ):
        """Test loading a session with existing history."""
        input = LoadSessionInput(
            role=mock_role,
            session_id=mock_session_id,
        )

        mock_agent_session = MagicMock()

        mock_history = MagicMock()
        mock_history.sdk_session_id = "sdk-session-123"
        mock_history.sdk_session_data = '{"messages": []}'

        # Set up the mock service
        mock_service = AsyncMock()
        mock_service.get_session.return_value = mock_agent_session
        mock_service.load_session_history.return_value = mock_history

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        result = await load_session_activity(input)

        assert result.found is True
        assert result.sdk_session_id == "sdk-session-123"
        assert result.sdk_session_data == '{"messages": []}'


class TestRunAgentActivity:
    """Tests for run_agent_activity Temporal activity."""

    @pytest.fixture
    def mock_executor_input(
        self,
        mock_role: Role,
        mock_session_id: uuid.UUID,
        mock_agent_config: AgentConfig,
    ) -> AgentExecutorInput:
        """Create a mock AgentExecutorInput for testing."""
        return AgentExecutorInput(
            session_id=mock_session_id,
            workspace_id=mock_role.workspace_id or uuid.uuid4(),
            user_prompt="Test prompt",
            config=mock_agent_config,
            role=mock_role,
            mcp_auth_token="mock-jwt-token",
            litellm_auth_token="mock-llm-token",
        )

    @pytest.mark.anyio
    async def test_successful_execution(self, mock_executor_input: AgentExecutorInput):
        """Test successful agent execution."""
        expected_result = AgentExecutorResult(success=True)

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.SandboxedAgentExecutor"
            ) as mock_executor_cls,
        ):
            mock_activity.heartbeat = MagicMock()
            mock_executor = MagicMock()
            mock_executor.run = AsyncMock(return_value=expected_result)
            mock_executor_cls.return_value = mock_executor

            result = await run_agent_activity(mock_executor_input)

            assert result == expected_result
            mock_executor_cls.assert_called_once_with(input=mock_executor_input)

    @pytest.mark.anyio
    async def test_returns_approval_requested_on_approval_interrupt(
        self, mock_executor_input: AgentExecutorInput
    ):
        """Test that approval_requested is returned when agent needs approval."""
        expected_result = AgentExecutorResult(
            success=True,
            approval_requested=True,
            approval_items=[],
        )

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.SandboxedAgentExecutor"
            ) as mock_executor_cls,
        ):
            mock_activity.heartbeat = MagicMock()
            mock_executor = MagicMock()
            mock_executor.run = AsyncMock(return_value=expected_result)
            mock_executor_cls.return_value = mock_executor

            result = await run_agent_activity(mock_executor_input)

            assert result.success is True
            assert result.approval_requested is True

    @pytest.mark.anyio
    async def test_handles_execution_error(
        self, mock_executor_input: AgentExecutorInput
    ):
        """Test that execution errors are captured in the result."""
        expected_result = AgentExecutorResult(
            success=False,
            error="Agent execution failed: timeout",
        )

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.SandboxedAgentExecutor"
            ) as mock_executor_cls,
        ):
            mock_activity.heartbeat = MagicMock()
            mock_executor = MagicMock()
            mock_executor.run = AsyncMock(return_value=expected_result)
            mock_executor_cls.return_value = mock_executor

            result = await run_agent_activity(mock_executor_input)

            assert result.success is False

    @pytest.mark.anyio
    async def test_sends_heartbeats(self, mock_executor_input: AgentExecutorInput):
        """Test that heartbeats are sent during execution."""
        expected_result = AgentExecutorResult(success=True)

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.SandboxedAgentExecutor"
            ) as mock_executor_cls,
        ):
            mock_activity.heartbeat = MagicMock()
            mock_executor = MagicMock()
            mock_executor.run = AsyncMock(return_value=expected_result)
            mock_executor_cls.return_value = mock_executor

            await run_agent_activity(mock_executor_input)

            # Should send heartbeat at start and end
            assert mock_activity.heartbeat.call_count >= 2
