"""Prompt API router for freezing and replaying chats."""

import asyncio
import uuid
from typing import Annotated

import orjson
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from tracecat.auth.credentials import RoleACL
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.prompt.models import (
    PromptCreate,
    PromptRead,
    PromptRunRequest,
    PromptRunResponse,
    PromptUpdate,
)
from tracecat.prompt.service import PromptService
from tracecat.redis.client import get_redis_client
from tracecat.types.auth import Role

router = APIRouter(prefix="/prompt", tags=["prompt"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.post("/", response_model=PromptRead)
async def create_prompt(
    request: PromptCreate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> PromptRead:
    """Freeze a chat into a reusable prompt."""
    prompt_service = PromptService(session, role)
    chat = await prompt_service.chats.get_chat(request.chat_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )
    prompt = await prompt_service.create_prompt(chat=chat)
    return PromptRead.model_validate(prompt, from_attributes=True)


@router.get("/", response_model=list[PromptRead])
async def list_prompts(
    role: WorkspaceUser,
    session: AsyncDBSession,
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of prompts to return"
    ),
) -> list[PromptRead]:
    """List prompts for the current workspace."""
    svc = PromptService(session, role)
    prompts = await svc.list_prompts(limit=limit)
    return [
        PromptRead.model_validate(prompt, from_attributes=True) for prompt in prompts
    ]


@router.get("/{prompt_id}", response_model=PromptRead)
async def get_prompt(
    prompt_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> PromptRead:
    """Get a prompt by ID."""
    svc = PromptService(session, role)
    prompt = await svc.get_prompt(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt not found",
        )
    return PromptRead.model_validate(prompt, from_attributes=True)


@router.patch("/{prompt_id}", response_model=PromptRead)
async def update_prompt(
    prompt_id: uuid.UUID,
    params: PromptUpdate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> PromptRead:
    """Update prompt properties."""
    svc = PromptService(session, role)
    prompt = await svc.get_prompt(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt not found",
        )

    prompt = await svc.update_prompt(
        prompt,
        title=params.title,
        content=params.content,
        tools=params.tools,
    )
    return PromptRead.model_validate(prompt, from_attributes=True)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    prompt_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> None:
    """Delete a prompt."""
    svc = PromptService(session, role)
    prompt = await svc.get_prompt(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt not found",
        )
    await svc.delete_prompt(prompt)


@router.post("/{prompt_id}/run", response_model=PromptRunResponse)
async def run_prompt(
    prompt_id: uuid.UUID,
    params: PromptRunRequest,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> PromptRunResponse:
    """Execute a prompt on multiple cases."""
    svc = PromptService(session, role)

    prompt = await svc.get_prompt(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt not found",
        )

    try:
        responses = await svc.run_prompt(prompt, params.entities)
        return PromptRunResponse(
            stream_urls={
                str(response.chat_id): response.stream_url for response in responses
            }
        )
    except Exception as e:
        logger.error(
            "Failed to run prompt",
            prompt_id=prompt_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to run prompt: {str(e)}",
        ) from e


@router.get("/{prompt_id}/case/{case_id}/stream")
async def stream_prompt_execution(
    request: Request,
    prompt_id: str,
    case_id: str,
    role: WorkspaceUser,
    session: AsyncDBSession,
):
    """Stream prompt execution events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    when a prompt is run on a case. It reuses the same Redis stream pattern
    as the chat service.
    """
    # Verify case exists and user has access to it
    case_uuid = uuid.UUID(case_id)
    svc = CasesService(session, role)
    case = await svc.get_case(case_uuid)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found or access denied",
        )

    stream_key = f"agent-stream:{case_id}"
    last_id = request.headers.get("Last-Event-ID", "0-0")

    logger.info(
        "Starting prompt execution stream",
        stream_key=stream_key,
        last_id=last_id,
        prompt_id=prompt_id,
        case_id=case_id,
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
                                    else:
                                        # Send the message
                                        data_json = orjson.dumps(data).decode()
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
            logger.info("Prompt execution stream ended", stream_key=stream_key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
