"""Chat API router for real-time AI agent interactions."""

import asyncio
import uuid
from typing import Annotated

import orjson
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from tracecat.api.chat_models import ChatRequest, ChatResponse
from tracecat.auth.credentials import RoleACL
from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.executor.client import ExecutorClient
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
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
    action_ref = str(uuid.uuid4())

    # Prepare agent arguments
    agent_args = {
        "user_prompt": request.message,
        "model_name": request.model_name,
        "model_provider": request.model_provider,
        "actions": request.actions,
        "workflow_run_id": conversation_id,
        "action_ref": action_ref,
    }

    if request.instructions:
        agent_args["instructions"] = request.instructions

    if request.context:
        # Add context as fixed arguments if provided
        agent_args["fixed_arguments"] = request.context

    # Execute the agent via the ExecutorClient
    executor = ExecutorClient(role=role)

    run_input = RunActionInput(
        task=ActionStatement(
            ref=action_ref,
            action="ai.agent",
            args=agent_args,
        ),
        exec_context={},
        run_context=RunContext(
            wf_id=conversation_id,
            wf_exec_id=conversation_id,
            wf_run_id=conversation_id,
            environment="default",
        ),
    )

    try:
        # Fire-and-forget execution
        _ = await executor.run_action_memory_backend(run_input)

        stream_url = f"/api/chat/stream/{conversation_id}/{action_ref}"

        return ChatResponse(
            stream_url=stream_url,
            conversation_id=conversation_id,
            action_ref=action_ref,
        )
    except Exception as e:
        logger.error(
            "Failed to start chat turn",
            conversation_id=conversation_id,
            action_ref=action_ref,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start chat turn: {str(e)}",
        ) from e


@router.get("/stream/{conversation_id}/{action_ref}")
async def stream_chat_events(
    request: Request,
    conversation_id: str,
    action_ref: str,
    role: WorkspaceUser,
):
    """Stream chat events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    using Server-Sent Events. It supports automatic reconnection via the
    Last-Event-ID header.
    """
    stream_key = f"agent-stream:{conversation_id}:{action_ref}"
    last_id = request.headers.get("Last-Event-ID", "0-0")

    logger.info(
        "Starting chat stream",
        stream_key=stream_key,
        last_id=last_id,
        conversation_id=conversation_id,
        action_ref=action_ref,
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
