"""Tests for PromptService functionality."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat, User, UserRole, Workspace
from tracecat.prompt.service import PromptService
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def prompt_service(session: AsyncSession, svc_role: Role) -> PromptService:
    """Return an instance of PromptService bound to the current session and role."""
    return PromptService(session=session, role=svc_role)


@pytest.fixture
async def chat_service(session: AsyncSession, svc_role: Role) -> ChatService:
    """Return an instance of ChatService bound to the current session and role."""
    return ChatService(session=session, role=svc_role)


@pytest.fixture
async def test_user(session: AsyncSession) -> AsyncGenerator[User, None]:
    """Create a test user."""
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password="$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",  # Secret123
        is_active=True,
        is_verified=True,
        role=UserRole.BASIC,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    try:
        yield user
    finally:
        # Cleanup
        await session.delete(user)
        await session.commit()


@pytest.fixture
async def test_chat(
    session: AsyncSession, svc_workspace: Workspace, test_user: User
) -> AsyncGenerator[Chat, None]:
    """Create a test chat with tools."""
    chat = Chat(
        title="Test Chat for Prompt",
        entity_type=ChatEntity.CASE,
        entity_id=uuid.uuid4(),
        user_id=test_user.id,
        owner_id=svc_workspace.id,
        tools=["core.http_request", "tools.slack.post_message"],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    try:
        yield chat
    finally:
        # Cleanup
        await session.delete(chat)
        await session.commit()


@pytest.fixture
def sample_chat_messages() -> list[ChatMessage]:
    """Create sample chat messages for testing."""
    return [
        ChatMessage(
            id=str(uuid.uuid4()),
            chat_id=str(uuid.uuid4()),
            message=ModelRequest(
                parts=[
                    UserPromptPart(
                        content="Investigate suspicious login from IP 192.168.1.100"
                    )
                ]
            ),
            role="user",
            created_at="2024-01-01T00:00:00Z",
        ),
        ChatMessage(
            id=str(uuid.uuid4()),
            chat_id=str(uuid.uuid4()),
            message=ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="core.http_request",
                        args={"url": "https://api.example.com/ip/192.168.1.100"},
                    )
                ]
            ),
            role="assistant",
            created_at="2024-01-01T00:00:01Z",
        ),
    ]


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class TestPromptService:
    """Test suite for PromptService."""

    @pytest.mark.anyio
    async def test_create_prompt_parallel_execution(
        self,
        prompt_service: PromptService,
        test_chat: Chat,
        sample_chat_messages: list[ChatMessage],
    ) -> None:
        """Test that _prompt_summary and _chat_to_prompt_title are executed in parallel."""
        # Track call times to verify parallel execution
        call_times: dict[str, float] = {}

        async def mock_prompt_summary(*args, **kwargs) -> str:
            """Mock prompt summary that records when it was called."""
            call_times["summary_start"] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)  # Simulate AI processing time
            call_times["summary_end"] = asyncio.get_event_loop().time()
            return "Test summary content"

        async def mock_chat_to_prompt_title(*args, **kwargs) -> str:
            """Mock title generation that records when it was called."""
            call_times["title_start"] = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)  # Simulate AI processing time
            call_times["title_end"] = asyncio.get_event_loop().time()
            return "Test runbook title"

        # Mock the ChatService to return our sample messages
        with patch.object(
            prompt_service.chats, "get_chat_messages", return_value=sample_chat_messages
        ):
            # Mock the AI generation methods
            with patch.object(
                prompt_service, "_prompt_summary", side_effect=mock_prompt_summary
            ) as mock_summary:
                with patch.object(
                    prompt_service,
                    "_chat_to_prompt_title",
                    side_effect=mock_chat_to_prompt_title,
                ) as mock_title:
                    # Create the prompt
                    prompt = await prompt_service.create_prompt(
                        chat=test_chat, meta={"case_title": "Suspicious Login"}
                    )

                    # Verify both methods were called
                    mock_summary.assert_called_once()
                    mock_title.assert_called_once()

                    # Verify parallel execution by checking overlapping time windows
                    # If executed in parallel, the start times should be very close
                    # and there should be overlap in execution
                    assert "summary_start" in call_times
                    assert "title_start" in call_times

                    # The methods should start nearly simultaneously (within 10ms)
                    start_diff = abs(
                        call_times["summary_start"] - call_times["title_start"]
                    )
                    assert start_diff < 0.01, (
                        f"Methods didn't start in parallel (diff: {start_diff}s)"
                    )

                    # There should be execution overlap
                    summary_duration = (
                        call_times["summary_end"] - call_times["summary_start"]
                    )
                    title_duration = call_times["title_end"] - call_times["title_start"]

                    # Both should take about 0.1s, showing they ran concurrently
                    assert summary_duration > 0.09, (
                        f"Summary took {summary_duration}s, expected ~0.1s"
                    )
                    assert title_duration > 0.09, (
                        f"Title took {title_duration}s, expected ~0.1s"
                    )

                    # Verify the prompt was created correctly
                    assert prompt is not None
                    assert prompt.title == "Test runbook title"
                    assert prompt.summary == "Test summary content"
                    assert prompt.chat_id == test_chat.id
                    assert prompt.tools == test_chat.tools

    @pytest.mark.anyio
    async def test_create_prompt_with_successful_ai_calls(
        self,
        prompt_service: PromptService,
        test_chat: Chat,
        sample_chat_messages: list[ChatMessage],
    ) -> None:
        """Test create_prompt when both AI calls succeed."""
        # Mock the ChatService
        with patch.object(
            prompt_service.chats, "get_chat_messages", return_value=sample_chat_messages
        ):
            # Mock successful AI calls
            with patch.object(
                prompt_service, "_prompt_summary", return_value="Generated summary"
            ):
                with patch.object(
                    prompt_service,
                    "_chat_to_prompt_title",
                    return_value="Generated title",
                ):
                    prompt = await prompt_service.create_prompt(chat=test_chat)

                    assert prompt.title == "Generated title"
                    assert prompt.summary == "Generated summary"
                    assert prompt.chat_id == test_chat.id
                    assert prompt.tools == test_chat.tools
                    assert "Task" in prompt.content
                    assert "Steps" in prompt.content

    @pytest.mark.anyio
    async def test_create_prompt_with_failed_summary(
        self,
        prompt_service: PromptService,
        test_chat: Chat,
        sample_chat_messages: list[ChatMessage],
    ) -> None:
        """Test create_prompt when summary generation fails."""
        with patch.object(
            prompt_service.chats, "get_chat_messages", return_value=sample_chat_messages
        ):
            # Mock summary failure
            with patch.object(
                prompt_service,
                "_prompt_summary",
                side_effect=Exception("AI service error"),
            ):
                with patch.object(
                    prompt_service,
                    "_chat_to_prompt_title",
                    return_value="Generated title",
                ):
                    prompt = await prompt_service.create_prompt(chat=test_chat)

                    assert prompt.title == "Generated title"
                    assert prompt.summary is None  # Should be None when summary fails
                    assert prompt.chat_id == test_chat.id

    @pytest.mark.anyio
    async def test_create_prompt_with_failed_title(
        self,
        prompt_service: PromptService,
        test_chat: Chat,
        sample_chat_messages: list[ChatMessage],
    ) -> None:
        """Test create_prompt when title generation fails."""
        with patch.object(
            prompt_service.chats, "get_chat_messages", return_value=sample_chat_messages
        ):
            with patch.object(
                prompt_service, "_prompt_summary", return_value="Generated summary"
            ):
                # Mock title failure
                with patch.object(
                    prompt_service,
                    "_chat_to_prompt_title",
                    side_effect=Exception("AI service error"),
                ):
                    prompt = await prompt_service.create_prompt(chat=test_chat)

                    assert (
                        prompt.title == test_chat.title
                    )  # Should fallback to chat title
                    assert prompt.summary == "Generated summary"
                    assert prompt.chat_id == test_chat.id

    @pytest.mark.anyio
    async def test_create_prompt_with_both_failures(
        self,
        prompt_service: PromptService,
        test_chat: Chat,
        sample_chat_messages: list[ChatMessage],
    ) -> None:
        """Test create_prompt when both AI calls fail."""
        with patch.object(
            prompt_service.chats, "get_chat_messages", return_value=sample_chat_messages
        ):
            # Mock both failures
            with patch.object(
                prompt_service,
                "_prompt_summary",
                side_effect=Exception("Summary error"),
            ):
                with patch.object(
                    prompt_service,
                    "_chat_to_prompt_title",
                    side_effect=Exception("Title error"),
                ):
                    prompt = await prompt_service.create_prompt(chat=test_chat)

                    # Should still create prompt with fallback values
                    assert prompt.title == test_chat.title
                    assert prompt.summary is None
                    assert prompt.chat_id == test_chat.id
                    assert prompt.tools == test_chat.tools
                    assert (
                        prompt.content is not None
                    )  # Content should still be generated

    @pytest.mark.anyio
    async def test_chat_to_prompt_title_generation(
        self, prompt_service: PromptService, test_chat: Chat
    ) -> None:
        """Test the _chat_to_prompt_title method with mocked AI response."""
        # Create a mock model config context manager
        mock_model_config = MagicMock()
        mock_model_config.name = "test-model"
        mock_model_config.provider = "test-provider"

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_model_config
        mock_context.__aexit__.return_value = None

        # Mock the agent run response
        mock_response = MagicMock()
        mock_response.output = "Investigate Suspicious Login"

        with patch(
            "tracecat.prompt.service.AgentManagementService"
        ) as MockAgentService:
            mock_service_instance = MockAgentService.return_value
            mock_service_instance.with_model_config.return_value = mock_context

            with patch("tracecat.prompt.service.get_model") as mock_get_model:
                with patch("tracecat.prompt.service.build_agent") as mock_build_agent:
                    mock_agent = AsyncMock()
                    mock_agent.run.return_value = mock_response
                    mock_build_agent.return_value = mock_agent

                    # Test the method
                    messages = [
                        ChatMessage(
                            id=str(uuid.uuid4()),
                            chat_id=str(test_chat.id),
                            message=ModelRequest(
                                parts=[UserPromptPart(content="Test user message")]
                            ),
                            role="user",
                            created_at="2024-01-01T00:00:00Z",
                        )
                    ]

                    title = await prompt_service._chat_to_prompt_title(
                        test_chat, {"case_title": "Security Alert"}, messages
                    )

                    # Verify the title was processed correctly (first letter capitalized)
                    assert title == "Investigate suspicious login"

                    # Verify the mocks were called
                    mock_get_model.assert_called_once_with(
                        "test-model", "test-provider"
                    )
                    mock_build_agent.assert_called_once()
                    mock_agent.run.assert_called_once()

    @pytest.mark.anyio
    async def test_prompt_summary_generation(
        self, prompt_service: PromptService
    ) -> None:
        """Test the _prompt_summary method with mocked AI response."""
        # Create mock model config
        mock_model_config = MagicMock()
        mock_model_config.name = "test-model"
        mock_model_config.provider = "test-provider"

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_model_config
        mock_context.__aexit__.return_value = None

        # Mock the agent run response
        mock_response = MagicMock()
        mock_response.output = """# Investigate Suspicious Login

## Objective
Investigate and respond to suspicious login attempts

## Tools
- core.http_request
- tools.slack.post_message

## Trigger
**Execute when**:
- Suspicious login detected from unknown IP

## Steps
1. Query IP reputation service
2. Notify security team via Slack"""

        with patch(
            "tracecat.prompt.service.AgentManagementService"
        ) as MockAgentService:
            mock_service_instance = MockAgentService.return_value
            mock_service_instance.with_model_config.return_value = mock_context

            with patch("tracecat.prompt.service.get_model"):
                with patch("tracecat.prompt.service.build_agent") as mock_build_agent:
                    mock_agent = AsyncMock()
                    mock_agent.run.return_value = mock_response
                    mock_build_agent.return_value = mock_agent

                    # Test the method
                    steps = """<Steps>
<Step type="user-prompt">
    <Content type="json">{"message": "Investigate login"}</Content>
</Step>
</Steps>"""

                    summary = await prompt_service._prompt_summary(
                        steps, ["core.http_request", "tools.slack.post_message"]
                    )

                    # Verify the summary was returned
                    assert "Investigate Suspicious Login" in summary
                    assert "Objective" in summary
                    assert "Tools" in summary
                    assert "Trigger" in summary
                    assert "Steps" in summary

    @pytest.mark.anyio
    async def test_get_prompt(
        self, prompt_service: PromptService, test_chat: Chat
    ) -> None:
        """Test retrieving a prompt by ID."""
        # First create a prompt
        with patch.object(prompt_service.chats, "get_chat_messages", return_value=[]):
            with patch.object(
                prompt_service, "_prompt_summary", return_value="Test summary"
            ):
                with patch.object(
                    prompt_service, "_chat_to_prompt_title", return_value="Test title"
                ):
                    created_prompt = await prompt_service.create_prompt(chat=test_chat)

        # Now retrieve it
        retrieved_prompt = await prompt_service.get_prompt(created_prompt.id)

        assert retrieved_prompt is not None
        assert retrieved_prompt.id == created_prompt.id
        assert retrieved_prompt.title == "Test title"
        assert retrieved_prompt.summary == "Test summary"

    @pytest.mark.anyio
    async def test_list_prompts(
        self, prompt_service: PromptService, test_chat: Chat
    ) -> None:
        """Test listing prompts for a workspace."""
        # Create multiple prompts
        with patch.object(prompt_service.chats, "get_chat_messages", return_value=[]):
            with patch.object(
                prompt_service, "_prompt_summary", return_value="Test summary"
            ):
                with patch.object(
                    prompt_service, "_chat_to_prompt_title"
                ) as mock_title:
                    # Create 3 prompts with different titles
                    mock_title.side_effect = ["Title 1", "Title 2", "Title 3"]

                    for _ in range(3):
                        await prompt_service.create_prompt(chat=test_chat)

        # List prompts
        prompts = await prompt_service.list_prompts(limit=10)

        assert len(prompts) >= 3  # At least our 3 prompts
        # Verify they're ordered by created_at desc (newest first)
        titles = [p.title for p in prompts[:3]]
        assert "Title 3" in titles
        assert "Title 2" in titles
        assert "Title 1" in titles

    @pytest.mark.anyio
    async def test_update_prompt(
        self, prompt_service: PromptService, test_chat: Chat
    ) -> None:
        """Test updating prompt properties."""
        # Create a prompt
        with patch.object(prompt_service.chats, "get_chat_messages", return_value=[]):
            with patch.object(prompt_service, "_prompt_summary", return_value=None):
                with patch.object(
                    prompt_service,
                    "_chat_to_prompt_title",
                    return_value="Original title",
                ):
                    prompt = await prompt_service.create_prompt(chat=test_chat)

        # Update the prompt
        updated_prompt = await prompt_service.update_prompt(
            prompt,
            title="Updated title",
            summary="Updated summary",
            tools=["new.tool"],
        )

        assert updated_prompt.title == "Updated title"
        assert updated_prompt.summary == "Updated summary"
        assert updated_prompt.tools == ["new.tool"]

        # Verify the update persisted
        retrieved = await prompt_service.get_prompt(prompt.id)
        assert retrieved.title == "Updated title"
        assert retrieved.summary == "Updated summary"
        assert retrieved.tools == ["new.tool"]

    @pytest.mark.anyio
    async def test_delete_prompt(
        self, prompt_service: PromptService, test_chat: Chat
    ) -> None:
        """Test deleting a prompt."""
        # Create a prompt
        with patch.object(prompt_service.chats, "get_chat_messages", return_value=[]):
            with patch.object(prompt_service, "_prompt_summary", return_value=None):
                with patch.object(
                    prompt_service, "_chat_to_prompt_title", return_value="Test title"
                ):
                    prompt = await prompt_service.create_prompt(chat=test_chat)

        prompt_id = prompt.id

        # Delete the prompt
        await prompt_service.delete_prompt(prompt)

        # Verify it's deleted
        retrieved = await prompt_service.get_prompt(prompt_id)
        assert retrieved is None
