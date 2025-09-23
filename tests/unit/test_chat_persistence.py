"""Test chat message persistence to database."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.chat.enums import MessageKind
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat, Workspace
from tracecat.types.auth import Role


@pytest.mark.anyio
async def test_append_message(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role
):
    """Test appending a message to a chat."""
    # Create a test chat (use svc_role.user_id or generate a test user id)
    test_user_id = svc_role.user_id or uuid.uuid4()
    chat = Chat(
        title="Test Chat",
        user_id=test_user_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=["tool1", "tool2"],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    # Use the provided role
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
async def test_list_messages(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role
):
    """Test listing messages from a chat."""
    # Create a test chat (use svc_role.user_id or generate a test user id)
    test_user_id = svc_role.user_id or uuid.uuid4()
    chat = Chat(
        title="Test Chat",
        user_id=test_user_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    # Use the provided role
    chat_service = ChatService(session, svc_role)

    # Add multiple messages
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
    # Verify messages are in order and content is preserved
    assert "First message" in str(retrieved_messages[0])
    assert "First response" in str(retrieved_messages[1])
    assert "Second message" in str(retrieved_messages[2])
    assert "Second response" in str(retrieved_messages[3])


@pytest.mark.anyio
async def test_redis_backfill(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role
):
    """Test backfilling messages from Redis."""
    # Create a test chat (use svc_role.user_id or generate a test user id)
    test_user_id = svc_role.user_id or uuid.uuid4()
    chat = Chat(
        title="Test Chat",
        user_id=test_user_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    # Use the provided role
    chat_service = ChatService(session, svc_role)

    # Mock Redis client with some messages
    mock_redis_client = AsyncMock()
    mock_redis_messages = [
        (
            "1234567890-0",
            {
                b"data": b'{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "Redis message 1"}]}'
            },
        ),
        (
            "1234567890-1",
            {
                b"data": b'{"t": "delta", "text": "Streaming delta"}'  # Should be skipped
            },
        ),
        (
            "1234567890-2",
            {
                b"data": b'{"kind": "response", "parts": [{"part_kind": "text", "content": "Redis response 1"}]}'
            },
        ),
        (
            "1234567890-3",
            {
                b"data": b'{"END": "STREAM"}'  # End marker, should be skipped
            },
        ),
    ]
    mock_redis_client.xrange = AsyncMock(return_value=mock_redis_messages)

    with patch(
        "tracecat.chat.service.get_redis_client", return_value=mock_redis_client
    ):
        # Trigger backfill by calling get_chat_messages on empty chat
        await chat_service.get_chat_messages(chat)

    # Verify backfill happened - should have 2 messages (deltas and end markers skipped)
    db_messages = await chat_service.list_messages(chat.id)
    assert len(db_messages) == 2
    assert "Redis message 1" in str(db_messages[0])
    assert "Redis response 1" in str(db_messages[1])


@pytest.mark.anyio
async def test_get_chat_messages_with_existing_data(
    session: AsyncSession, svc_workspace: Workspace, svc_role: Role
):
    """Test that get_chat_messages returns DB data when available."""
    # Create a test chat (use svc_role.user_id or generate a test user id)
    test_user_id = svc_role.user_id or uuid.uuid4()
    chat = Chat(
        title="Test Chat",
        user_id=test_user_id,
        entity_type="case",
        entity_id=uuid.uuid4(),
        owner_id=svc_workspace.id,
        tools=[],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    # Use the provided role
    chat_service = ChatService(session, svc_role)

    # Add a message to DB
    message = ModelRequest(parts=[UserPromptPart(content="Database message")])
    await chat_service.append_message(
        chat_id=chat.id,
        message=message,
        kind=MessageKind.CHAT_MESSAGE,
    )

    # Mock Redis client to ensure it's NOT called for backfill
    mock_redis_client = AsyncMock()
    mock_redis_client.xrange = AsyncMock()

    with patch(
        "tracecat.chat.service.get_redis_client", return_value=mock_redis_client
    ):
        # Get messages - should come from DB, not Redis
        messages = await chat_service.get_chat_messages(chat)

    # Verify we got the DB message and Redis was not called for backfill
    assert len(messages) == 1
    assert "Database message" in str(messages[0].message)
    mock_redis_client.xrange.assert_not_called()


@pytest.mark.anyio
async def test_message_kind_enum():
    """Test that MessageKind enum values are correct."""
    assert MessageKind.CHAT_MESSAGE.value == "chat-message"
    assert str(MessageKind.CHAT_MESSAGE) == "chat-message"
