"""Chat API router for real-time AI agent interactions."""

import asyncio
from typing import Annotated

import orjson
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from tracecat_registry.integrations.agents.builder import agent

from tracecat.api.chat_models import ChatRequest, ChatResponse
from tracecat.auth.credentials import RoleACL
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.secrets import secrets_manager
from tracecat.types.auth import Role

router = APIRouter(prefix="/chat", tags=["chat"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.post("/{conversation_id}")
async def start_chat_turn(
    conversation_id: str,
    request: ChatRequest,
    role: WorkspaceUser,
) -> ChatResponse:
    """Start a new chat turn with an AI agent.

    This endpoint initiates an AI agent execution and returns a stream URL
    for real-time streaming of the agent's processing steps.
    """

    # Prepare agent arguments
    agent_args = {
        "user_prompt": request.message,
        "model_name": "gpt-4o",
        "model_provider": request.model_provider,
        "actions": request.actions,
        "workflow_run_id": conversation_id,
    }

    if request.instructions:
        agent_args["instructions"] = request.instructions

    if request.context:
        # Add context as fixed arguments if provided
        agent_args["fixed_arguments"] = request.context

    try:
        # Fire-and-forget execution using the agent function directly
        secrets_manager.set(
            "OPENAI_API_KEY",
            "<REDACTED_API_KEY>",
        )
        _ = asyncio.create_task(agent(**agent_args))

        stream_url = f"/api/chat/stream/{conversation_id}"

        return ChatResponse(
            stream_url=stream_url,
            conversation_id=conversation_id,
        )
    except Exception as e:
        logger.error(
            "Failed to start chat turn",
            conversation_id=conversation_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start chat turn: {str(e)}",
        ) from e


@router.get("/stream/{conversation_id}")
async def stream_chat_events(
    request: Request,
    conversation_id: str,
    role: WorkspaceUser,
):
    """Stream chat events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    using Server-Sent Events. It supports automatic reconnection via the
    Last-Event-ID header.
    """
    stream_key = f"agent-stream:{conversation_id}"
    last_id = request.headers.get("Last-Event-ID", "0-0")

    logger.info(
        "Starting chat stream",
        stream_key=stream_key,
        last_id=last_id,
        conversation_id=conversation_id,
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
                                    data = orjson.loads(fields["d"])

                                    # Check for end-of-stream marker
                                    if data.get("__end__") == 1:
                                        yield f"id: {message_id}\nevent: end\ndata: {{}}\n\n"
                                        return

                                    # Send the message
                                    data_json = orjson.dumps(data).decode()
                                    yield f"id: {message_id}\nevent: message\ndata: {data_json}\n\n"

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
