"""Test chat message persistence to database."""

import uuid

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.chat.enums import MessageKind
from tracecat.chat.models import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat, User, Workspace
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def test_user(session: AsyncSession, svc_workspace: Workspace) -> User:
    """Create a test user for chat tests."""
    user = User(
        id=uuid.uuid4(),
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


@pytest.mark.anyio
async def test_append_message(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test appending a single message to a chat."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=["tool1", "tool2"],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Test appending a user message
    user_message = ModelRequest(parts=[UserPromptPart(content="Hello, AI!")])
    db_message = await chat_service.append_message(
        chat_id=chat.id,
        message=user_message,
        kind=MessageKind.CHAT_MESSAGE,
    )

    assert db_message is not None
    assert db_message.chat_id == chat.id
    assert db_message.kind == MessageKind.CHAT_MESSAGE.value
    assert db_message.owner_id == svc_workspace.id
    assert "Hello, AI!" in str(db_message.data)


@pytest.mark.anyio
async def test_append_messages_batch(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test batch appending multiple messages to a chat."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Create multiple messages
    messages = [
        ModelRequest(parts=[UserPromptPart(content="First message")]),
        ModelResponse(parts=[TextPart(content="First response")]),
        ModelRequest(parts=[UserPromptPart(content="Second message")]),
        ModelResponse(parts=[TextPart(content="Second response")]),
    ]

    # Batch append messages
    await chat_service.append_messages(
        chat_id=chat.id,
        messages=messages,
        kind=MessageKind.CHAT_MESSAGE,
    )

    # Retrieve and verify
    retrieved_messages = await chat_service.list_messages(chat.id)

    assert len(retrieved_messages) == 4
    assert "First message" in str(retrieved_messages[0])
    assert "First response" in str(retrieved_messages[1])
    assert "Second message" in str(retrieved_messages[2])
    assert "Second response" in str(retrieved_messages[3])


@pytest.mark.anyio
async def test_append_messages_empty_list(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test that appending an empty list of messages does nothing."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Append empty list (should be a no-op)
    await chat_service.append_messages(
        chat_id=chat.id,
        messages=[],
        kind=MessageKind.CHAT_MESSAGE,
    )

    # Verify no messages were created
    retrieved_messages = await chat_service.list_messages(chat.id)
    assert len(retrieved_messages) == 0


@pytest.mark.anyio
async def test_list_messages(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test listing messages from a chat in order."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Add multiple messages individually
    messages = [
        ModelRequest(parts=[UserPromptPart(content="First message")]),
        ModelResponse(parts=[TextPart(content="First response")]),
        ModelRequest(parts=[UserPromptPart(content="Second message")]),
        ModelResponse(parts=[TextPart(content="Second response")]),
    ]

    for msg in messages:
        await chat_service.append_message(
            chat_id=chat.id,
            message=msg,
            kind=MessageKind.CHAT_MESSAGE,
        )

    # List messages
    retrieved_messages = await chat_service.list_messages(chat.id)

    assert len(retrieved_messages) == 4
    # Verify messages are in correct order and content is preserved
    assert isinstance(retrieved_messages[0], ModelRequest)
    assert isinstance(retrieved_messages[1], ModelResponse)
    assert isinstance(retrieved_messages[2], ModelRequest)
    assert isinstance(retrieved_messages[3], ModelResponse)
    assert "First message" in str(retrieved_messages[0])
    assert "First response" in str(retrieved_messages[1])
    assert "Second message" in str(retrieved_messages[2])
    assert "Second response" in str(retrieved_messages[3])


@pytest.mark.anyio
async def test_get_chat_messages(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test get_chat_messages returns ChatMessage objects with proper IDs."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Add messages
    messages = [
        ModelRequest(parts=[UserPromptPart(content="User message 1")]),
        ModelResponse(parts=[TextPart(content="Assistant response 1")]),
        ModelRequest(parts=[UserPromptPart(content="User message 2")]),
    ]

    await chat_service.append_messages(
        chat_id=chat.id,
        messages=messages,
        kind=MessageKind.CHAT_MESSAGE,
    )

    # Get messages using get_chat_messages
    chat_messages = await chat_service.get_chat_messages(chat)

    assert len(chat_messages) == 3
    # Verify each is a ChatMessage with ID
    for idx, chat_msg in enumerate(chat_messages):
        assert isinstance(chat_msg, ChatMessage)
        assert chat_msg.id == str(idx)
        assert chat_msg.message is not None
        assert isinstance(chat_msg.message, ModelRequest | ModelResponse)


@pytest.mark.anyio
async def test_chat_message_from_db(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test ChatMessage.from_db() class method converts DB messages correctly."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Add a message
    message = ModelRequest(parts=[UserPromptPart(content="Test message")])
    db_message = await chat_service.append_message(
        chat_id=chat.id,
        message=message,
        kind=MessageKind.CHAT_MESSAGE,
    )

    # Convert using from_db
    chat_message = ChatMessage.from_db(db_message)

    assert isinstance(chat_message, ChatMessage)
    assert chat_message.id == str(db_message.id)
    assert isinstance(chat_message.message, ModelRequest)
    assert "Test message" in str(chat_message.message)


@pytest.mark.anyio
async def test_get_chat_messages_empty_chat(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test that get_chat_messages returns empty list for chat with no messages."""
    chat = Chat(
        title="Empty Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Get messages from empty chat
    messages = await chat_service.get_chat_messages(chat)

    assert len(messages) == 0
    assert isinstance(messages, list)


@pytest.mark.anyio
async def test_message_kind_enum():
    """Test that MessageKind enum values are correct."""
    assert MessageKind.CHAT_MESSAGE.value == "chat-message"
    assert str(MessageKind.CHAT_MESSAGE) == "chat-message"


@pytest.mark.anyio
async def test_list_messages_ordering(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test that messages are returned in chronological order (oldest first)."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Add messages one at a time with slight ordering
    msg1 = ModelRequest(parts=[UserPromptPart(content="Message 1")])
    msg2 = ModelRequest(parts=[UserPromptPart(content="Message 2")])
    msg3 = ModelRequest(parts=[UserPromptPart(content="Message 3")])

    await chat_service.append_message(chat_id=chat.id, message=msg1)
    await chat_service.append_message(chat_id=chat.id, message=msg2)
    await chat_service.append_message(chat_id=chat.id, message=msg3)

    # Retrieve messages
    messages = await chat_service.list_messages(chat.id)

    # Verify order
    assert len(messages) == 3
    assert "Message 1" in str(messages[0])
    assert "Message 2" in str(messages[1])
    assert "Message 3" in str(messages[2])


@pytest.mark.anyio
async def test_mixed_message_types(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role, test_user: User
):
    """Test storing and retrieving different message types (request/response)."""
    chat = Chat(
        title="Test Chat",
        user_id=test_user.id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    chat_service = ChatService(session, svc_role)

    # Add different types of messages
    user_msg = ModelRequest(parts=[UserPromptPart(content="User question")])
    assistant_msg = ModelResponse(parts=[TextPart(content="Assistant answer")])

    await chat_service.append_message(chat_id=chat.id, message=user_msg)
    await chat_service.append_message(chat_id=chat.id, message=assistant_msg)

    # Retrieve and verify types are preserved
    messages = await chat_service.list_messages(chat.id)

    assert len(messages) == 2
    assert isinstance(messages[0], ModelRequest)
    assert isinstance(messages[1], ModelResponse)
    assert "User question" in str(messages[0])
    assert "Assistant answer" in str(messages[1])
