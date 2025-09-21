"""Chat API router for real-time AI agent interactions."""

import asyncio
import uuid
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.agent.executor.deps import WorkspaceUser, get_executor
from tracecat.agent.runtime import ModelMessageTA
from tracecat.agent.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)
from tracecat.chat.models import (
    ChatCreate,
    ChatMessage,
    ChatRead,
    ChatRequest,
    ChatResponse,
    ChatUpdate,
    ChatWithMessages,
)
from tracecat.chat.service import ChatService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/")
async def create_chat(
    request: ChatCreate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatRead:
    """Create a new chat associated with an entity."""
    chat_service = ChatService(session, role)
    chat = await chat_service.create_chat(
        title=request.title,
        entity_type=request.entity_type,
        entity_id=request.entity_id,
        tools=request.tools,
    )
    return ChatRead.model_validate(chat, from_attributes=True)


@router.get("/")
async def list_chats(
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_type: str | None = Query(None, description="Filter by entity type"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of chats to return"
    ),
) -> list[ChatRead]:
    """List chats for the current workspace with optional filtering."""
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID is required",
        )

    svc = ChatService(session, role)
    chats = await svc.list_chats(
        user_id=role.user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )

    chats = [ChatRead.model_validate(chat, from_attributes=True) for chat in chats]
    return chats


@router.get("/{chat_id}")
async def get_chat(
    chat_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatWithMessages:
    """Get a chat with its message history."""
    svc = ChatService(session, role)

    # Get chat metadata
    chat = await svc.get_chat(chat_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    # Get messages from Redis
    messages = await svc.get_chat_messages(chat)

    chat_data = ChatRead.model_validate(chat, from_attributes=True)
    return ChatWithMessages(
        **chat_data.model_dump(),
        messages=[ChatMessage(id=msg.id, message=msg.message) for msg in messages],
    )


@router.patch("/{chat_id}")
async def update_chat(
    chat_id: uuid.UUID,
    request: ChatUpdate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatRead:
    """Update chat properties."""
    svc = ChatService(session, role)
    chat = await svc.get_chat(chat_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    chat = await svc.update_chat(
        chat,
        tools=request.tools,
        title=request.title,
    )
    return ChatRead.model_validate(chat, from_attributes=True)


@router.post("/{chat_id}")
async def start_chat_turn(
    chat_id: uuid.UUID,
    request: ChatRequest,
    role: WorkspaceUser,
    session: AsyncDBSession,
    executor: Annotated[BaseAgentExecutor, Depends(get_executor)],
) -> ChatResponse:
    """Start a new chat turn with an AI agent.

    This endpoint initiates an AI agent execution and returns a stream URL
    for real-time streaming of the agent's processing steps.
    """
    chat_service = ChatService(session, role)

    try:
        return await chat_service.start_chat_turn(
            chat_id=chat_id,
            request=request,
            executor=executor,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(
            "Failed to start chat turn",
            chat_id=chat_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start chat turn: {str(e)}",
        ) from e


@router.get("/{chat_id}/stream")
async def stream_chat_events(
    role: WorkspaceUser,
    request: Request,
    chat_id: uuid.UUID,
):
    """Stream chat events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    using Server-Sent Events. It supports automatic reconnection via the
    Last-Event-ID header.
    """
    stream_key = f"agent-stream:{chat_id}"
    last_id = request.headers.get("Last-Event-ID", "0-0")

    logger.info(
        "Starting chat stream",
        stream_key=stream_key,
        last_id=last_id,
        chat_id=chat_id,
    )

    async def event_generator():
        try:
            redis_client = await get_redis_client()

            # Send initial connection event
            yield f"id: {last_id}\nevent: connected\ndata: {{}}\n\n"

            current_id = last_id

            while not await request.is_disconnected():
                try:
                    # Read from Redis stream with blocking
                    result = await redis_client.xread(
                        streams={stream_key: current_id},
                        count=10,
                        block=1000,  # Block for 1 second
                    )

                    if result:
                        for _stream_name, messages in result:
                            for message_id, fields in messages:
                                try:
                                    data = orjson.loads(fields[DATA_KEY])

                                    # Check for end-of-stream marker
                                    if data.get(END_TOKEN) == END_TOKEN_VALUE:
                                        yield f"id: {message_id}\nevent: end\ndata: {{}}\n\n"
                                    else:
                                        # Send the message
                                        # Validate the message is a valid ModelMessage
                                        # perf: delete this
                                        validated_msg = ModelMessageTA.validate_python(
                                            data
                                        )
                                        data_json = orjson.dumps(validated_msg).decode()
                                        yield f"id: {message_id}\nevent: message\ndata: {data_json}\n\n"

                                    # Ensure in all cases we advance the current ID
                                    current_id = message_id

                                except Exception as e:
                                    logger.warning(
                                        "Failed to process stream message",
                                        error=str(e),
                                        message_id=message_id,
                                    )
                                    continue

                    # Send heartbeat to keep connection alive
                    await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error("Error reading from Redis stream", error=str(e))
                    yield 'event: error\ndata: {"error": "Stream read error"}\n\n'
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error("Fatal error in stream generator", error=str(e))
            yield 'event: error\ndata: {"error": "Fatal stream error"}\n\n'
        finally:
            logger.info("Chat stream ended", stream_key=stream_key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
