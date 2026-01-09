"""Agent Session API router for unified session management.

This router consolidates chat and session endpoints into a unified /agent/sessions API.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from tracecat.agent.adapter import vercel
from tracecat.agent.session.schemas import (
    AgentSessionCreate,
    AgentSessionRead,
    AgentSessionReadVercel,
    AgentSessionReadWithMessages,
    AgentSessionUpdate,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.agent.types import StreamKey
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.chat.schemas import ChatRequest
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(prefix="/agent/sessions", tags=["agent-sessions"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.post("")
async def create_session(
    request: AgentSessionCreate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> AgentSessionRead:
    """Create a new agent session associated with an entity."""
    svc = AgentSessionService(session, role)
    agent_session = await svc.create_session(request)
    return AgentSessionRead.model_validate(agent_session, from_attributes=True)


@router.get("")
async def list_sessions(
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_type: AgentSessionEntity | None = Query(
        None, description="Filter by entity type"
    ),
    entity_id: uuid.UUID | None = Query(None, description="Filter by entity ID"),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of sessions to return"
    ),
) -> list[AgentSessionRead]:
    """List agent sessions for the current workspace with optional filtering."""
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID is required",
        )

    svc = AgentSessionService(session, role)
    sessions = await svc.list_sessions(
        user_id=role.user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )

    return [AgentSessionRead.model_validate(s, from_attributes=True) for s in sessions]


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> AgentSessionReadWithMessages:
    """Get an agent session with its message history."""
    svc = AgentSessionService(session, role)

    # Get session metadata
    agent_session = await svc.get_session(session_id)
    if not agent_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Get messages using unified message retrieval
    messages = await svc.list_messages(session_id)

    res = AgentSessionReadWithMessages(
        id=agent_session.id,
        workspace_id=agent_session.workspace_id,
        title=agent_session.title,
        user_id=agent_session.user_id,
        entity_type=agent_session.entity_type,
        entity_id=agent_session.entity_id,
        tools=agent_session.tools,
        agent_preset_id=agent_session.agent_preset_id,
        harness_type=agent_session.harness_type,
        created_at=agent_session.created_at,
        updated_at=agent_session.updated_at,
        last_stream_id=agent_session.last_stream_id,
        messages=messages,
    )
    logger.info("Session read", session_id=agent_session.id, messages=len(messages))
    return res


@router.get("/{session_id}/vercel")
async def get_session_vercel(
    session_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> AgentSessionReadVercel:
    """Get an agent session with its message history in Vercel format."""
    svc = AgentSessionService(session, role)
    agent_session = await svc.get_session(session_id)
    if not agent_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Get messages and convert to Vercel format
    messages = await svc.list_messages(session_id)
    ui_messages = vercel.convert_chat_messages_to_ui(messages)

    return AgentSessionReadVercel(
        id=agent_session.id,
        workspace_id=agent_session.workspace_id,
        title=agent_session.title,
        user_id=agent_session.user_id,
        entity_type=agent_session.entity_type,
        entity_id=agent_session.entity_id,
        tools=agent_session.tools,
        agent_preset_id=agent_session.agent_preset_id,
        harness_type=agent_session.harness_type,
        created_at=agent_session.created_at,
        updated_at=agent_session.updated_at,
        last_stream_id=agent_session.last_stream_id,
        messages=ui_messages,
    )


@router.patch("/{session_id}")
async def update_session(
    session_id: uuid.UUID,
    params: AgentSessionUpdate,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> AgentSessionRead:
    """Update session properties."""
    svc = AgentSessionService(session, role)
    agent_session = await svc.get_session(session_id)
    if not agent_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    updated = await svc.update_session(agent_session, params=params)
    return AgentSessionRead.model_validate(updated, from_attributes=True)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> None:
    """Delete an agent session."""
    svc = AgentSessionService(session, role)
    agent_session = await svc.get_session(session_id)
    if not agent_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await svc.delete_session(agent_session)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    request: ChatRequest,
    role: WorkspaceUser,
    session: AsyncDBSession,
    http_request: Request,
) -> StreamingResponse:
    """Send a message to the agent session with streaming response.

    This endpoint combines message sending with streaming response,
    compatible with Vercel's AI SDK useChat hook. It:
    1. Accepts Vercel UI message format or continuation requests
    2. Starts the agent execution
    3. Streams the response back in Vercel's data protocol format
    """
    try:
        svc = AgentSessionService(session, role)
        workspace_id = role.workspace_id
        if workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace access required",
            )

        # Run session turn (spawns DurableAgentWorkflow)
        await svc.run_turn(
            session_id=session_id,
            request=request,
        )

        agent_session = await svc.get_session(session_id)
        if agent_session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

        # Set up streaming with Vercel format
        start_id = agent_session.last_stream_id or http_request.headers.get(
            "Last-Event-ID", "0-0"
        )

        logger.info(
            "Starting Vercel streaming session",
            session_id=session_id,
            start_id=start_id,
        )

        # Create stream and return with Vercel format
        stream = await AgentStream.new(agent_session.id, workspace_id)
        return StreamingResponse(
            stream.sse(http_request.is_disconnected, last_id=start_id, format="vercel"),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Transfer-Encoding": "chunked",
                "Content-Encoding": "none",
                "Connection": "keep-alive",
                "Keep-Alive": "timeout=120",
                "Pragma": "no-cache",
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
            "Failed to start streaming session",
            session_id=session_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start streaming session: {str(e)}",
        ) from e


@router.get("/{session_id}/stream")
async def stream_session_events(
    role: WorkspaceUser,
    request: Request,
    session_id: uuid.UUID,
    format: StreamFormat = Query(
        default="vercel", description="Streaming format (e.g. 'vercel')"
    ),
):
    """Stream session events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    using Server-Sent Events. It supports automatic reconnection via the
    Last-Event-ID header.
    """
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

        # Try to get last_stream_id from session, but don't fail if session doesn't exist yet.
        # This handles the race condition where frontend connects before session is created.
    last_stream_id: str | None = None
    async with AgentSessionService.with_session(role=role) as svc:
        agent_session = await svc.get_session(session_id)
        if agent_session is not None:
            last_stream_id = agent_session.last_stream_id

    start_id = last_stream_id or request.headers.get("Last-Event-ID", "0-0")
    stream_key = StreamKey(workspace_id, session_id)
    logger.info(
        "Starting session stream",
        stream_key=stream_key,
        last_id=start_id,
        session_id=session_id,
    )

    stream = await AgentStream.new(session_id, workspace_id)
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=120",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    return StreamingResponse(
        stream.sse(request.is_disconnected, last_id=start_id, format=format),
        media_type="text/event-stream",
        headers=headers,
    )
