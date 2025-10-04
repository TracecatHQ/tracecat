"""Chat API router for real-time AI agent interactions."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic_ai.messages import AgentStreamEvent

from tracecat.agent.adapter import vercel
from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.agent.executor.deps import WorkspaceUser, get_executor
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.chat.models import (
    ChatCreate,
    ChatMessage,
    ChatRead,
    ChatReadMinimal,
    ChatReadVercel,
    ChatRequest,
    ChatResponse,
    ChatUpdate,
)
from tracecat.chat.service import ChatService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
async def create_chat(
    request: ChatCreate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatReadMinimal:
    """Create a new chat associated with an entity."""
    chat_service = ChatService(session, role)
    chat = await chat_service.create_chat(
        title=request.title,
        entity_type=request.entity_type,
        entity_id=request.entity_id,
        tools=request.tools,
    )
    return ChatReadMinimal.model_validate(chat, from_attributes=True)


@router.get("")
async def list_chats(
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_type: str | None = Query(None, description="Filter by entity type"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of chats to return"
    ),
) -> list[ChatReadMinimal]:
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

    chats = [
        ChatReadMinimal.model_validate(chat, from_attributes=True) for chat in chats
    ]
    return chats


@router.get("/{chat_id}")
async def get_chat(
    chat_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatRead:
    """Get a chat with its message history."""
    svc = ChatService(session, role)

    # Get chat metadata
    chat = await svc.get_chat(chat_id, with_messages=True)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    res = ChatRead(
        id=chat.id,
        title=chat.title,
        user_id=chat.user_id,
        entity_type=chat.entity_type,
        entity_id=chat.entity_id,
        tools=chat.tools,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_stream_id=chat.last_stream_id,
        messages=[ChatMessage.from_db(message) for message in chat.messages],
    )
    logger.info("Chat read", chat_id=chat.id, messages=len(chat.messages))
    return res


@router.get("/{chat_id}/vercel")
async def get_chat_vercel(
    chat_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatReadVercel:
    """Get a chat with its message history in Vercel format."""

    # Get chat with ModelMessage format

    svc = ChatService(session, role)
    chat = await svc.get_chat(chat_id, with_messages=True)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    # Convert messages to UIMessage format
    messages = [ChatMessage.from_db(message) for message in chat.messages]
    ui_messages = vercel.convert_model_messages_to_ui(messages)

    # Return ChatReadVercel with converted messages
    return ChatReadVercel(
        id=chat.id,
        title=chat.title,
        user_id=chat.user_id,
        entity_type=chat.entity_type,
        entity_id=chat.entity_id,
        tools=chat.tools,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_stream_id=chat.last_stream_id,
        messages=ui_messages,
    )


@router.patch("/{chat_id}")
async def update_chat(
    chat_id: uuid.UUID,
    request: ChatUpdate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> ChatReadMinimal:
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
    return ChatReadMinimal.model_validate(chat, from_attributes=True)


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
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
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


@router.post("/{chat_id}/vercel")
async def chat_with_vercel_streaming(
    chat_id: uuid.UUID,
    request: ChatRequest,
    role: WorkspaceUser,
    session: AsyncDBSession,
    executor: Annotated[BaseAgentExecutor, Depends(get_executor)],
    http_request: Request,
) -> StreamingResponse:
    """Vercel AI SDK compatible chat endpoint with streaming.

    This endpoint combines chat turn initiation with streaming response,
    compatible with Vercel's AI SDK useChat hook. It:
    1. Accepts Vercel UI message format
    2. Starts the agent execution
    3. Streams the response back in Vercel's data protocol format
    """

    try:
        svc = ChatService(session, role)
        # Start the chat turn (this will spawn the agent execution)
        await svc.start_chat_turn(
            chat_id=chat_id,
            request=request,
            executor=executor,
        )

        # Get the chat to retrieve last stream ID
        chat = await svc.get_chat(chat_id)
        if chat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat not found",
            )

        # Set up streaming with Vercel format
        start_id = chat.last_stream_id or http_request.headers.get(
            "Last-Event-ID", "0-0"
        )

        logger.info(
            "Starting Vercel streaming chat",
            chat_id=chat_id,
            start_id=start_id,
        )

        # Create stream and return with Vercel format
        stream = AgentStream(await get_redis_client(), chat_id)
        return StreamingResponse(
            stream.sse(http_request, last_id=start_id, format="vercel"),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
                "x-vercel-ai-ui-message-stream": "v1",
            },
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception(
            "Failed to start Vercel streaming chat",
            chat_id=chat_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start streaming chat: {str(e)}",
        ) from e


@router.get("/{chat_id}/stream", response_model=list[AgentStreamEvent])
async def stream_chat_events(
    role: WorkspaceUser,
    request: Request,
    chat_id: uuid.UUID,
    format: StreamFormat = Query(
        default="basic", description="Streaming format (e.g. 'vercel')"
    ),
):
    """Stream chat events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    using Server-Sent Events. It supports automatic reconnection via the
    Last-Event-ID header.
    """
    stream_key = f"agent-stream:{chat_id}"

    async with ChatService.with_session(role=role) as chat_svc:
        chat = await chat_svc.get_chat(chat_id)
        if chat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat not found",
            )
        last_stream_id = chat.last_stream_id

    start_id = last_stream_id or request.headers.get("Last-Event-ID", "0-0")

    logger.info(
        "Starting chat stream",
        stream_key=stream_key,
        last_id=start_id,
        chat_id=chat_id,
    )

    stream = AgentStream(await get_redis_client(), chat_id)
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    return StreamingResponse(
        stream.sse(request, last_id=start_id, format=format),
        media_type="text/event-stream",
        headers=headers,
    )
