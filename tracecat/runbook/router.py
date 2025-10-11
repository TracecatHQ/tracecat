"""Runbook API router for creating and executing runbooks."""

import asyncio
import uuid
from typing import Annotated

import orjson
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError

from tracecat.agent.types import StreamKey
from tracecat.auth.credentials import RoleACL
from tracecat.cases.service import CasesService
from tracecat.chat.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.runbook.models import (
    RunbookCreate,
    RunbookExecuteRequest,
    RunbookExecuteResponse,
    RunbookRead,
    RunbookUpdate,
)
from tracecat.runbook.service import RunbookService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


def _is_constraint_violation(error: IntegrityError, constraint_name: str) -> bool:
    """Return True when the integrity error matches the named DB constraint."""

    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None)
    diag_constraint = getattr(diag, "constraint_name", None)

    if diag_constraint == constraint_name:
        return True

    # Fallback to string inspection when the driver doesn't expose diag data.
    if orig and constraint_name in str(orig):
        return True

    return constraint_name in str(error)


router = APIRouter(prefix="/runbooks", tags=["runbook"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.post("", response_model=RunbookRead)
async def create_runbook(
    params: RunbookCreate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> RunbookRead:
    """Create a new runbook."""
    runbook_service = RunbookService(session, role)
    try:
        runbook = await runbook_service.create_runbook(params)
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
    except IntegrityError as e:
        # Check if it's the alias uniqueness constraint
        if _is_constraint_violation(e, "uq_prompt_alias_owner_id"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Runbook with alias '{params.alias}' already exists in this workspace",
            ) from e
        # Re-raise for other integrity errors
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Runbook creation failed due to a conflict",
        ) from e
    return RunbookRead.model_validate(runbook, from_attributes=True)


@router.get("", response_model=list[RunbookRead])
async def list_runbooks(
    role: WorkspaceUser,
    session: AsyncDBSession,
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of runbooks to return"
    ),
    sort_by: str = Query(
        "created_at",
        description="Field to sort by: 'created_at' or 'updated_at'",
        pattern="^(created_at|updated_at)$",
    ),
    order: str = Query(
        "desc",
        description="Sort order: 'asc' or 'desc'",
        pattern="^(asc|desc)$",
    ),
) -> list[RunbookRead]:
    """List runbooks for the current workspace."""
    svc = RunbookService(session, role)
    runbooks = await svc.list_runbooks(limit=limit, sort_by=sort_by, order=order)
    return [
        RunbookRead.model_validate(runbook, from_attributes=True)
        for runbook in runbooks
    ]


@router.get("/{runbook_id}", response_model=RunbookRead)
async def get_runbook(
    runbook_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> RunbookRead:
    """Get a runbook by ID."""
    svc = RunbookService(session, role)
    runbook = await svc.get_runbook_by_id(runbook_id)
    if not runbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runbook not found",
        )
    return RunbookRead.model_validate(runbook, from_attributes=True)


@router.patch("/{runbook_id}", response_model=RunbookRead)
async def update_runbook(
    runbook_id: uuid.UUID,
    params: RunbookUpdate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> RunbookRead:
    """Update runbook properties."""
    svc = RunbookService(session, role)
    runbook = await svc.get_runbook_by_id(runbook_id)
    if not runbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runbook not found",
        )

    try:
        runbook = await svc.update_runbook(runbook, params)
    except IntegrityError as e:
        # Check if it's the alias uniqueness constraint
        if _is_constraint_violation(e, "uq_prompt_alias_owner_id"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Runbook with alias '{params.alias}' already exists in this workspace",
            ) from e
        # Re-raise for other integrity errors
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Runbook update failed due to a conflict",
        ) from e
    return RunbookRead.model_validate(runbook, from_attributes=True)


@router.delete("/{runbook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runbook(
    runbook_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> None:
    """Delete a runbook."""
    svc = RunbookService(session, role)
    runbook = await svc.get_runbook_by_id(runbook_id)
    if not runbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runbook not found",
        )
    await svc.delete_runbook(runbook)


@router.post("/{runbook_id}/execute", response_model=RunbookExecuteResponse)
async def execute_runbook(
    runbook_id: uuid.UUID,
    params: RunbookExecuteRequest,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> RunbookExecuteResponse:
    """Execute a runbook on multiple cases."""
    svc = RunbookService(session, role)

    runbook = await svc.get_runbook_by_id(runbook_id)
    if not runbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runbook not found",
        )
    try:
        return await svc.execute_runbook(runbook, case_ids=params.case_ids)
    except Exception as e:
        logger.error(
            "Failed to run runbook",
            runbook_id=runbook_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run runbook",
        ) from e


@router.get("/{runbook_id}/case/{case_id}/stream")
async def stream_runbook_execution(
    request: Request,
    runbook_id: str,
    case_id: str,
    role: WorkspaceUser,
    session: AsyncDBSession,
):
    """Stream runbook execution events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    when a runbook is run on a case. It reuses the same Redis stream pattern
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

    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    stream_key = StreamKey(workspace_id, case_uuid)
    last_id = request.headers.get("Last-Event-ID", "0-0")

    logger.info(
        "Starting runbook execution stream",
        stream_key=stream_key,
        last_id=last_id,
        runbook_id=runbook_id,
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
                                    data = orjson.loads(fields[DATA_KEY])

                                    # Check for end-of-stream marker
                                    if data.get(END_TOKEN) == END_TOKEN_VALUE:
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
            logger.info("Runbook execution stream ended", stream_key=stream_key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
