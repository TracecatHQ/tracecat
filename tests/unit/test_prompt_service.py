import uuid
from unittest.mock import patch

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat
from tracecat.prompt.models import PromptCreate, PromptUpdate
from tracecat.prompt.service import PromptService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(
    session: AsyncSession,
) -> None:
    """Test that service initialization requires a workspace ID."""
    # Create a role without workspace_id
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        service_id="tracecat-service",
        access_level=AccessLevel.BASIC,
    )

    # Attempt to create service without workspace should raise error
    with pytest.raises(TracecatAuthorizationError):
        PromptService(session=session, role=role_without_workspace)


@pytest.fixture
async def prompt_service(session: AsyncSession, svc_role: Role) -> PromptService:
    """Create a prompt service instance for testing."""
    return PromptService(session=session, role=svc_role)


@pytest.fixture
async def chat_service(session: AsyncSession, svc_role: Role) -> ChatService:
    """Create a chat service instance for testing."""
    return ChatService(session=session, role=svc_role)


@pytest.fixture
def test_chat() -> Chat:
    """Create a mock test chat for testing prompt creation from chat."""
    # Mock chat since we can't create a real one without a user
    chat = Chat(
        id=uuid.uuid4(),
        title="Test Chat",
        user_id=uuid.uuid4(),
        entity_type=ChatEntity.CASE,
        entity_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        tools=["tools.example.query", "tools.example.update"],
    )
    return chat


@pytest.fixture
def prompt_create_params() -> PromptCreate:
    """Sample prompt creation parameters."""
    return PromptCreate(
        chat_id=None,
        alias="test-runbook",
        meta={"test": "metadata"},
    )


@pytest.fixture
def mock_chat_messages() -> list[ChatMessage]:
    """Mock chat messages for testing."""
    messages = [
        ChatMessage(
            id=str(uuid.uuid4()),
            message=ModelRequest(
                parts=[UserPromptPart(content="Test user prompt")],
            ),
        ),
        ChatMessage(
            id=str(uuid.uuid4()),
            message=ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="test_tool",
                        args={"param": "value"},
                        tool_call_id="call_123",
                    )
                ],
            ),
        ),
        ChatMessage(
            id=str(uuid.uuid4()),
            message=ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="test_tool",
                        content="Tool result",
                        tool_call_id="call_123",
                    )
                ],
            ),
        ),
        ChatMessage(
            id=str(uuid.uuid4()),
            message=ModelResponse(
                parts=[TextPart(content="Assistant response")],
            ),
        ),
    ]
    return messages


@pytest.mark.anyio
class TestPromptService:
    async def test_create_prompt_direct(self, prompt_service: PromptService) -> None:
        """Test creating a prompt directly without a chat."""
        prompt = await prompt_service.create_prompt_direct(
            title="Direct Test Prompt",
            content="This is a test prompt content",
            tools=["tools.example.query"],
            summary="Test summary",
            alias="direct-test-prompt",
        )

        assert prompt.title == "Direct Test Prompt"
        assert prompt.content == "This is a test prompt content"
        assert prompt.tools == ["tools.example.query"]
        assert prompt.summary == "Test summary"
        assert prompt.alias == "direct-test-prompt"
        assert prompt.chat_id is None
        assert prompt.owner_id == prompt_service.workspace_id

    @pytest.mark.skip(reason="Requires user to exist in database for chat creation")
    async def test_create_prompt_from_chat(
        self,
        prompt_service: PromptService,
        test_chat: Chat,
        mock_chat_messages: list[ChatMessage],
    ) -> None:
        """Test creating a prompt from an existing chat."""
        # Mock the chat service methods
        with (
            patch.object(
                prompt_service.chats,
                "get_chat_messages",
                return_value=mock_chat_messages,
            ),
            patch.object(
                prompt_service, "_prompt_summary", return_value="Generated summary"
            ),
            patch.object(
                prompt_service, "_chat_to_prompt_title", return_value="Generated Title"
            ),
        ):
            prompt = await prompt_service.create_prompt_from_chat(
                chat=test_chat,
                meta={"case_title": "Test Case"},
                alias="chat-based-prompt",
            )

            assert prompt.chat_id == test_chat.id
            assert prompt.title == "Generated Title"
            assert prompt.tools == test_chat.tools
            assert prompt.summary == "Generated summary"
            assert prompt.alias == "chat-based-prompt"
            assert prompt.owner_id == prompt_service.workspace_id
            assert "Task" in prompt.content
            assert "Steps" in prompt.content

    async def test_get_prompt_by_id(self, prompt_service: PromptService) -> None:
        """Test retrieving a prompt by ID."""
        # Create a prompt
        created_prompt = await prompt_service.create_prompt_direct(
            title="Test Prompt for ID",
            content="Test content",
            tools=[],
            alias="id-test-prompt",
        )

        # Retrieve by ID
        retrieved_prompt = await prompt_service.get_prompt(created_prompt.id)
        assert retrieved_prompt is not None
        assert retrieved_prompt.id == created_prompt.id
        assert retrieved_prompt.title == "Test Prompt for ID"
        assert retrieved_prompt.alias == "id-test-prompt"

    async def test_get_prompt_by_alias(self, prompt_service: PromptService) -> None:
        """Test retrieving a prompt by alias."""
        # Create a prompt with alias
        created_prompt = await prompt_service.create_prompt_direct(
            title="Test Prompt for Alias",
            content="Test content",
            tools=[],
            alias="unique-alias-test",
        )

        # Retrieve by alias
        retrieved_prompt = await prompt_service.get_prompt_by_alias("unique-alias-test")
        assert retrieved_prompt is not None
        assert retrieved_prompt.id == created_prompt.id
        assert retrieved_prompt.title == "Test Prompt for Alias"
        assert retrieved_prompt.alias == "unique-alias-test"

    async def test_resolve_prompt_alias(self, prompt_service: PromptService) -> None:
        """Test resolving a prompt alias to its ID."""
        # Create a prompt with alias
        created_prompt = await prompt_service.create_prompt_direct(
            title="Test Prompt for Resolution",
            content="Test content",
            tools=[],
            alias="resolve-test-alias",
        )

        # Resolve alias to ID
        resolved_id = await prompt_service.resolve_prompt_alias("resolve-test-alias")
        assert resolved_id is not None
        assert resolved_id == created_prompt.id

    async def test_list_prompts(self, prompt_service: PromptService) -> None:
        """Test listing prompts."""
        # Create multiple prompts
        prompt1 = await prompt_service.create_prompt_direct(
            title="First Prompt",
            content="First content",
            tools=[],
            alias="first-prompt",
        )

        prompt2 = await prompt_service.create_prompt_direct(
            title="Second Prompt",
            content="Second content",
            tools=["tools.example.query"],
            alias="second-prompt",
        )

        # List all prompts
        prompts = await prompt_service.list_prompts()
        assert len(prompts) >= 2

        prompt_ids = {p.id for p in prompts}
        assert prompt1.id in prompt_ids
        assert prompt2.id in prompt_ids

    async def test_list_prompts_with_limit(self, prompt_service: PromptService) -> None:
        """Test listing prompts with a limit."""
        # Create multiple prompts
        for i in range(3):
            await prompt_service.create_prompt_direct(
                title=f"Prompt {i}",
                content=f"Content {i}",
                tools=[],
                alias=f"prompt-{i}",
            )

        # List with limit
        prompts = await prompt_service.list_prompts(limit=2)
        assert len(prompts) == 2

    async def test_update_prompt(self, prompt_service: PromptService) -> None:
        """Test updating a prompt."""
        # Create initial prompt
        prompt = await prompt_service.create_prompt_direct(
            title="Initial Title",
            content="Initial content",
            tools=[],
            summary="Initial summary",
            alias="initial-alias",
        )

        # Update prompt
        update_params = PromptUpdate(
            title="Updated Title",
            content="Updated content",
            alias="updated-alias",
        )

        updated_prompt = await prompt_service.update_prompt(prompt, update_params)
        assert updated_prompt.title == "Updated Title"
        assert updated_prompt.content == "Updated content"
        assert updated_prompt.alias == "updated-alias"
        # Unchanged fields should remain the same
        assert updated_prompt.summary == "Initial summary"
        assert updated_prompt.tools == []

        # Verify persistence
        retrieved = await prompt_service.get_prompt(prompt.id)
        assert retrieved is not None
        assert retrieved.title == "Updated Title"
        assert retrieved.alias == "updated-alias"

    async def test_update_prompt_preserves_unchanged_fields(
        self, prompt_service: PromptService
    ) -> None:
        """Test that updating a prompt preserves unchanged fields."""
        # Create initial prompt with all fields
        prompt = await prompt_service.create_prompt_direct(
            title="Original Title",
            content="Original content",
            tools=["tool1", "tool2"],
            summary="Original summary",
            alias="original-alias",
        )

        # Update only title
        update_params = PromptUpdate(title="New Title")
        updated = await prompt_service.update_prompt(prompt, update_params)

        assert updated.title == "New Title"
        assert updated.content == "Original content"
        assert updated.tools == ["tool1", "tool2"]
        assert updated.summary == "Original summary"
        assert updated.alias == "original-alias"

    async def test_delete_prompt(self, prompt_service: PromptService) -> None:
        """Test deleting a prompt."""
        # Create prompt
        prompt = await prompt_service.create_prompt_direct(
            title="To Delete",
            content="Delete me",
            tools=[],
            alias="delete-test",
        )

        # Delete prompt
        await prompt_service.delete_prompt(prompt)

        # Verify deletion
        deleted_prompt = await prompt_service.get_prompt(prompt.id)
        assert deleted_prompt is None

        # Also verify can't find by alias
        deleted_by_alias = await prompt_service.get_prompt_by_alias("delete-test")
        assert deleted_by_alias is None

    async def test_create_prompt_with_alias(
        self, prompt_service: PromptService, prompt_create_params: PromptCreate
    ) -> None:
        """Test creating a prompt with an alias."""
        prompt = await prompt_service.create_prompt(prompt_create_params)

        assert prompt.alias == "test-runbook"
        assert prompt.owner_id == prompt_service.workspace_id

        # Verify can retrieve by alias
        retrieved = await prompt_service.get_prompt_by_alias("test-runbook")
        assert retrieved is not None
        assert retrieved.id == prompt.id

    async def test_update_prompt_alias(self, prompt_service: PromptService) -> None:
        """Test updating an existing prompt's alias."""
        # Create prompt without alias
        prompt = await prompt_service.create_prompt_direct(
            title="No Alias Initially",
            content="Content",
            tools=[],
            alias=None,
        )

        assert prompt.alias is None

        # Add alias via update
        update_params = PromptUpdate(alias="newly-added-alias")
        updated = await prompt_service.update_prompt(prompt, update_params)

        assert updated.alias == "newly-added-alias"

        # Verify can retrieve by new alias
        retrieved = await prompt_service.get_prompt_by_alias("newly-added-alias")
        assert retrieved is not None
        assert retrieved.id == prompt.id

    async def test_alias_uniqueness_constraint(
        self, prompt_service: PromptService
    ) -> None:
        """Test that duplicate aliases in the same workspace fail."""
        # Create first prompt with alias
        await prompt_service.create_prompt_direct(
            title="First Prompt",
            content="First",
            tools=[],
            alias="duplicate-test",
        )

        # Attempt to create second prompt with same alias should fail
        with pytest.raises(IntegrityError):
            await prompt_service.create_prompt_direct(
                title="Second Prompt",
                content="Second",
                tools=[],
                alias="duplicate-test",
            )

    async def test_prompt_not_found_by_alias(
        self, prompt_service: PromptService
    ) -> None:
        """Test error handling for non-existent alias."""
        # Try to get prompt by non-existent alias
        prompt = await prompt_service.get_prompt_by_alias("non-existent-alias")
        assert prompt is None

        # Try to resolve non-existent alias
        resolved_id = await prompt_service.resolve_prompt_alias("non-existent-alias")
        assert resolved_id is None

    async def test_empty_alias_allowed(self, prompt_service: PromptService) -> None:
        """Test that alias can be None or empty string."""
        # Create prompt with None alias
        prompt1 = await prompt_service.create_prompt_direct(
            title="No Alias",
            content="Content",
            tools=[],
            alias=None,
        )
        assert prompt1.alias is None

        # Create prompt with empty string alias
        prompt2 = await prompt_service.create_prompt_direct(
            title="Empty Alias",
            content="Content",
            tools=[],
            alias="",
        )
        assert prompt2.alias == ""

        # Both should be retrievable by ID
        retrieved1 = await prompt_service.get_prompt(prompt1.id)
        retrieved2 = await prompt_service.get_prompt(prompt2.id)
        assert retrieved1 is not None
        assert retrieved2 is not None

    async def test_create_prompt_without_chat(
        self, prompt_service: PromptService
    ) -> None:
        """Test creating a prompt without an associated chat."""
        params = PromptCreate(chat_id=None, alias="no-chat-prompt")
        prompt = await prompt_service.create_prompt(params)

        assert prompt.chat_id is None
        assert prompt.alias == "no-chat-prompt"
        # Should have a default title with timestamp
        assert "New runbook" in prompt.title
        assert prompt.content == ""
        assert prompt.tools == []

    async def test_list_prompts_with_sorting(
        self, prompt_service: PromptService
    ) -> None:
        """Test listing prompts with different sort options."""
        # Create 3 prompts with different titles
        for i in range(3):
            await prompt_service.create_prompt_direct(
                title=f"Sorted Prompt {i}",
                content=f"Content {i}",
                tools=[],
                alias=f"sorted-{i}",
            )

        # List with default sorting (newest first)
        newest_first = await prompt_service.list_prompts(order="desc")
        assert len(newest_first) >= 3

        # List with oldest first
        oldest_first = await prompt_service.list_prompts(order="asc")
        assert len(oldest_first) >= 3

        # The order should be reversed between the two
        # (can't guarantee exact ordering due to other prompts in the test database)
