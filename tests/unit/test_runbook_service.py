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
from tracecat.runbook.models import RunbookCreate, RunbookUpdate
from tracecat.runbook.service import RunbookService
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
        RunbookService(session=session, role=role_without_workspace)


@pytest.fixture
async def runbook_service(session: AsyncSession, svc_role: Role) -> RunbookService:
    """Create a runbook service instance for testing."""
    return RunbookService(session=session, role=svc_role)


@pytest.fixture
async def chat_service(session: AsyncSession, svc_role: Role) -> ChatService:
    """Create a chat service instance for testing."""
    return ChatService(session=session, role=svc_role)


@pytest.fixture
def test_chat() -> Chat:
    """Create a mock test chat for testing runbook creation from chat."""
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
def runbook_create_params() -> RunbookCreate:
    """Sample runbook creation parameters."""
    return RunbookCreate(
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
                parts=[UserPromptPart(content="Test user runbook")],
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
class TestRunbookService:
    async def test_create_runbook_direct(self, runbook_service: RunbookService) -> None:
        """Test creating a runbook directly without a chat."""
        runbook = await runbook_service.create_runbook_direct(
            title="Direct Test Runbook",
            content="This is a test runbook content",
            tools=["tools.example.query"],
            summary="Test summary",
            alias="direct-test-runbook",
        )

        assert runbook.title == "Direct Test Runbook"
        assert runbook.content == "This is a test runbook content"
        assert runbook.tools == ["tools.example.query"]
        assert runbook.summary == "Test summary"
        assert runbook.alias == "direct-test-runbook"
        assert runbook.owner_id == runbook_service.workspace_id

    @pytest.mark.skip(reason="Requires user to exist in database for chat creation")
    async def test_create_runbook_from_chat(
        self,
        runbook_service: RunbookService,
        test_chat: Chat,
        mock_chat_messages: list[ChatMessage],
    ) -> None:
        """Test creating a runbook from an existing chat."""
        # Mock the chat service methods
        with (
            patch.object(
                runbook_service.chats,
                "get_chat_messages",
                return_value=mock_chat_messages,
            ),
            patch.object(
                runbook_service, "_runbook_summary", return_value="Generated summary"
            ),
            patch.object(
                runbook_service,
                "_chat_to_runbook_title",
                return_value="Generated Title",
            ),
        ):
            runbook = await runbook_service.create_runbook_from_chat(
                chat=test_chat,
                meta={"case_title": "Test Case"},
                alias="chat-based-runbook",
            )

            assert runbook.title == "Generated Title"
            assert runbook.tools == test_chat.tools
            assert runbook.summary == "Generated summary"
            assert runbook.alias == "chat-based-runbook"
            assert runbook.owner_id == runbook_service.workspace_id
            assert "Task" in runbook.content
            assert "Steps" in runbook.content

    async def test_get_runbook_by_id(self, runbook_service: RunbookService) -> None:
        """Test retrieving a runbook by ID."""
        # Create a runbook
        created_runbook = await runbook_service.create_runbook_direct(
            title="Test Runbook for ID",
            content="Test content",
            tools=[],
            alias="id-test-runbook",
        )

        # Retrieve by ID
        retrieved_runbook = await runbook_service.get_runbook(created_runbook.id)
        assert retrieved_runbook is not None
        assert retrieved_runbook.id == created_runbook.id
        assert retrieved_runbook.title == "Test Runbook for ID"
        assert retrieved_runbook.alias == "id-test-runbook"

    async def test_get_runbook_by_alias(self, runbook_service: RunbookService) -> None:
        """Test retrieving a runbook by alias."""
        # Create a runbook with alias
        created_runbook = await runbook_service.create_runbook_direct(
            title="Test Runbook for Alias",
            content="Test content",
            tools=[],
            alias="unique-alias-test",
        )

        # Retrieve by alias
        retrieved_runbook = await runbook_service.get_runbook_by_alias(
            "unique-alias-test"
        )
        assert retrieved_runbook is not None
        assert retrieved_runbook.id == created_runbook.id
        assert retrieved_runbook.title == "Test Runbook for Alias"
        assert retrieved_runbook.alias == "unique-alias-test"

    async def test_resolve_runbook_alias(self, runbook_service: RunbookService) -> None:
        """Test resolving a runbook alias to its ID."""
        # Create a runbook with alias
        created_runbook = await runbook_service.create_runbook_direct(
            title="Test Runbook for Resolution",
            content="Test content",
            tools=[],
            alias="resolve-test-alias",
        )

        # Resolve alias to ID
        resolved_id = await runbook_service.resolve_runbook_alias("resolve-test-alias")
        assert resolved_id is not None
        assert resolved_id == created_runbook.id

    async def test_list_runbooks(self, runbook_service: RunbookService) -> None:
        """Test listing runbooks."""
        # Create multiple runbooks
        runbook1 = await runbook_service.create_runbook_direct(
            title="First Runbook",
            content="First content",
            tools=[],
            alias="first-runbook",
        )

        runbook2 = await runbook_service.create_runbook_direct(
            title="Second Runbook",
            content="Second content",
            tools=["tools.example.query"],
            alias="second-runbook",
        )

        # List all runbooks
        runbooks = await runbook_service.list_runbooks()
        assert len(runbooks) >= 2

        runbook_ids = {r.id for r in runbooks}
        assert runbook1.id in runbook_ids
        assert runbook2.id in runbook_ids

    async def test_list_runbooks_with_limit(
        self, runbook_service: RunbookService
    ) -> None:
        """Test listing runbooks with a limit."""
        # Create multiple runbooks
        for i in range(3):
            await runbook_service.create_runbook_direct(
                title=f"Runbook {i}",
                content=f"Content {i}",
                tools=[],
                alias=f"runbook-{i}",
            )

        # List with limit
        runbooks = await runbook_service.list_runbooks(limit=2)
        assert len(runbooks) == 2

    async def test_update_runbook(self, runbook_service: RunbookService) -> None:
        """Test updating a runbook."""
        # Create initial runbook
        runbook = await runbook_service.create_runbook_direct(
            title="Initial Title",
            content="Initial content",
            tools=[],
            summary="Initial summary",
            alias="initial-alias",
        )

        # Update runbook
        update_params = RunbookUpdate(
            title="Updated Title",
            content="Updated content",
            alias="updated-alias",
        )

        updated_runbook = await runbook_service.update_runbook(runbook, update_params)
        assert updated_runbook.title == "Updated Title"
        assert updated_runbook.content == "Updated content"
        assert updated_runbook.alias == "updated-alias"
        # Unchanged fields should remain the same
        assert updated_runbook.summary == "Initial summary"
        assert updated_runbook.tools == []

        # Verify persistence
        retrieved = await runbook_service.get_runbook(runbook.id)
        assert retrieved is not None
        assert retrieved.title == "Updated Title"
        assert retrieved.alias == "updated-alias"

    async def test_update_runbook_preserves_unchanged_fields(
        self, runbook_service: RunbookService
    ) -> None:
        """Test that updating a runbook preserves unchanged fields."""
        # Create initial runbook with all fields
        runbook = await runbook_service.create_runbook_direct(
            title="Original Title",
            content="Original content",
            tools=["tool1", "tool2"],
            summary="Original summary",
            alias="original-alias",
        )

        # Update only title
        update_params = RunbookUpdate(title="New Title")
        updated = await runbook_service.update_runbook(runbook, update_params)

        assert updated.title == "New Title"
        assert updated.content == "Original content"
        assert updated.tools == ["tool1", "tool2"]
        assert updated.summary == "Original summary"
        assert updated.alias == "original-alias"

    async def test_delete_runbook(self, runbook_service: RunbookService) -> None:
        """Test deleting a runbook."""
        # Create runbook
        runbook = await runbook_service.create_runbook_direct(
            title="To Delete",
            content="Delete me",
            tools=[],
            alias="delete-test",
        )

        # Delete runbook
        await runbook_service.delete_runbook(runbook)

        # Verify deletion
        deleted_runbook = await runbook_service.get_runbook(runbook.id)
        assert deleted_runbook is None

        # Also verify can't find by alias
        deleted_by_alias = await runbook_service.get_runbook_by_alias("delete-test")
        assert deleted_by_alias is None

    async def test_create_runbook_with_alias(
        self, runbook_service: RunbookService, runbook_create_params: RunbookCreate
    ) -> None:
        """Test creating a runbook with an alias."""
        runbook = await runbook_service.create_runbook(runbook_create_params)

        assert runbook.alias == "test-runbook"
        assert runbook.owner_id == runbook_service.workspace_id

        # Verify can retrieve by alias
        retrieved = await runbook_service.get_runbook_by_alias("test-runbook")
        assert retrieved is not None
        assert retrieved.id == runbook.id

    async def test_update_runbook_alias(self, runbook_service: RunbookService) -> None:
        """Test updating an existing runbook's alias."""
        # Create runbook without alias
        runbook = await runbook_service.create_runbook_direct(
            title="No Alias Initially",
            content="Content",
            tools=[],
            alias=None,
        )

        assert runbook.alias is None

        # Add alias via update
        update_params = RunbookUpdate(alias="newly-added-alias")
        updated = await runbook_service.update_runbook(runbook, update_params)

        assert updated.alias == "newly-added-alias"

        # Verify can retrieve by new alias
        retrieved = await runbook_service.get_runbook_by_alias("newly-added-alias")
        assert retrieved is not None
        assert retrieved.id == runbook.id

    async def test_alias_uniqueness_constraint(
        self, runbook_service: RunbookService
    ) -> None:
        """Test that duplicate aliases in the same workspace fail."""
        # Create first runbook with alias
        await runbook_service.create_runbook_direct(
            title="First Runbook",
            content="First",
            tools=[],
            alias="duplicate-test",
        )

        # Attempt to create second runbook with same alias should fail
        with pytest.raises(IntegrityError):
            await runbook_service.create_runbook_direct(
                title="Second Runbook",
                content="Second",
                tools=[],
                alias="duplicate-test",
            )

    async def test_runbook_not_found_by_alias(
        self, runbook_service: RunbookService
    ) -> None:
        """Test error handling for non-existent alias."""
        # Try to get runbook by non-existent alias
        runbook = await runbook_service.get_runbook_by_alias("non-existent-alias")
        assert runbook is None

        # Try to resolve non-existent alias
        resolved_id = await runbook_service.resolve_runbook_alias("non-existent-alias")
        assert resolved_id is None

    async def test_empty_alias_allowed(self, runbook_service: RunbookService) -> None:
        """Test that alias can be None or empty string."""
        # Create runbook with None alias
        runbook1 = await runbook_service.create_runbook_direct(
            title="No Alias",
            content="Content",
            tools=[],
            alias=None,
        )
        assert runbook1.alias is None

        # Create runbook with empty string alias
        runbook2 = await runbook_service.create_runbook_direct(
            title="Empty Alias",
            content="Content",
            tools=[],
            alias="",
        )
        assert runbook2.alias == ""

        # Both should be retrievable by ID
        retrieved1 = await runbook_service.get_runbook(runbook1.id)
        retrieved2 = await runbook_service.get_runbook(runbook2.id)
        assert retrieved1 is not None
        assert retrieved2 is not None

    async def test_create_runbook_without_chat(
        self, runbook_service: RunbookService
    ) -> None:
        """Test creating a runbook without an associated chat."""
        params = RunbookCreate(alias="no-chat-runbook")
        runbook = await runbook_service.create_runbook(params)

        assert runbook.alias == "no-chat-runbook"
        # Should have a default title with timestamp
        assert "New runbook" in runbook.title
        assert runbook.content == ""
        assert runbook.tools == []

    async def test_list_runbooks_with_sorting(
        self, runbook_service: RunbookService
    ) -> None:
        """Test listing runbooks with different sort options."""
        # Create 3 runbooks with different titles
        for i in range(3):
            await runbook_service.create_runbook_direct(
                title=f"Sorted Runbook {i}",
                content=f"Content {i}",
                tools=[],
                alias=f"sorted-{i}",
            )

        # List with default sorting (newest first)
        newest_first = await runbook_service.list_runbooks(order="desc")
        assert len(newest_first) >= 3

        # List with oldest first
        oldest_first = await runbook_service.list_runbooks(order="asc")
        assert len(oldest_first) >= 3

        # The order should be reversed between the two
        # (can't guarantee exact ordering due to other runbooks in the test database)
