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
    AgentSessionForkRequest,
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
from tracecat.chat.schemas import (
    ChatRead,
    ChatReadMinimal,
    ChatReadVercel,
    ChatRequest,
)
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
    exclude_entity_types: list[AgentSessionEntity] | None = Query(
        None, description="Entity types to exclude from results"
    ),
    parent_session_id: uuid.UUID | None = Query(
        None, description="Filter by parent session ID (for finding forked sessions)"
    ),
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of sessions to return"
    ),
) -> list[AgentSessionRead | ChatReadMinimal]:
    """List agent sessions for the current workspace with optional filtering.

    Returns a list of sessions including both active AgentSessions and legacy
    Chat records. Legacy chats have is_readonly=True.
    """
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID is required",
        )

    svc = AgentSessionService(session, role)
    return await svc.list_sessions(
        created_by=role.user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        exclude_entity_types=exclude_entity_types,
        parent_session_id=parent_session_id,
        limit=limit,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> AgentSessionReadWithMessages | ChatRead:
    """Get an agent session or legacy chat with its message history.

    Legacy chats have is_readonly=True.
    """
    svc = AgentSessionService(session, role)

    # Try AgentSession first
    agent_session = await svc.get_session(session_id)
    if agent_session:
        messages = await svc.list_messages(session_id)
        logger.info("Session read", session_id=agent_session.id, messages=len(messages))
        return AgentSessionReadWithMessages(
            id=agent_session.id,
            workspace_id=agent_session.workspace_id,
            title=agent_session.title,
            created_by=agent_session.created_by,
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

    # Try legacy Chat (user_id remains for legacy Chat model)
    legacy_chat = await svc.get_legacy_chat(session_id)
    if legacy_chat:
        messages = await svc.list_messages(session_id)
        logger.info(
            "Legacy chat read", session_id=legacy_chat.id, messages=len(messages)
        )
        return ChatRead(
            id=legacy_chat.id,
            title=legacy_chat.title,
            user_id=legacy_chat.user_id,
            entity_type=legacy_chat.entity_type,
            entity_id=legacy_chat.entity_id,
            tools=legacy_chat.tools or [],
            agent_preset_id=legacy_chat.agent_preset_id,
            created_at=legacy_chat.created_at,
            updated_at=legacy_chat.updated_at,
            last_stream_id=legacy_chat.last_stream_id,
            messages=messages,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Session not found",
    )


@router.get("/{session_id}/vercel")
async def get_session_vercel(
    session_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> AgentSessionReadVercel | ChatReadVercel:
    """Get an agent session or legacy chat with message history in Vercel format.

    Legacy chats have is_readonly=True.
    """
    svc = AgentSessionService(session, role)

    # Try AgentSession first
    agent_session = await svc.get_session(session_id)
    if agent_session:
        messages = await svc.list_messages(session_id)
        ui_messages = vercel.convert_chat_messages_to_ui(messages)
        return AgentSessionReadVercel(
            id=agent_session.id,
            workspace_id=agent_session.workspace_id,
            title=agent_session.title,
            created_by=agent_session.created_by,
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

    # Try legacy Chat (user_id remains for legacy Chat model)
    legacy_chat = await svc.get_legacy_chat(session_id)
    if legacy_chat:
        messages = await svc.list_messages(session_id)
        ui_messages = vercel.convert_chat_messages_to_ui(messages)
        return ChatReadVercel(
            id=legacy_chat.id,
            title=legacy_chat.title,
            user_id=legacy_chat.user_id,
            entity_type=legacy_chat.entity_type,
            entity_id=legacy_chat.entity_id,
            tools=legacy_chat.tools or [],
            agent_preset_id=legacy_chat.agent_preset_id,
            created_at=legacy_chat.created_at,
            updated_at=legacy_chat.updated_at,
            last_stream_id=legacy_chat.last_stream_id,
            messages=ui_messages,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Session not found",
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

    # Check if this is a legacy chat (read-only)
    if await svc.is_legacy_session(session_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Legacy chat sessions are read-only and cannot be modified",
        )

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

    # Check if this is a legacy chat (read-only)
    if await svc.is_legacy_session(session_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Legacy chat sessions are read-only and cannot be deleted",
        )

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

        # Check if this is a legacy chat (read-only)
        if await svc.is_legacy_session(session_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Legacy chat sessions are read-only and cannot receive new messages",
            )

        agent_session = await svc.get_session(session_id)
        if agent_session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

        # Use last_stream_id if available (valid cursor from previous turn),
        # otherwise "$" to only read new events (avoids replaying old events
        # when cursor wasn't updated due to race conditions).
        start_id = agent_session.last_stream_id or "$"

        # Run session turn (spawns DurableAgentWorkflow)
        await svc.run_turn(
            session_id=session_id,
            request=request,
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
        logger.error(
            "Failed to start streaming session",
            session_id=session_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start streaming session",
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


@router.post("/{session_id}/fork")
async def fork_session(
    session_id: uuid.UUID,
    role: WorkspaceUser,
    session: AsyncDBSession,
    request: AgentSessionForkRequest | None = None,
) -> AgentSessionRead:
    """Fork an existing session to continue conversation post-decision.

    Creates a new session linked to the parent session, allowing users
    to ask the agent for context after making approval decisions.

    Set entity_type to 'approval' for inbox forks to hide from main chat list.
    """
    try:
        svc = AgentSessionService(session, role)
        entity_type = request.entity_type if request else None
        forked = await svc.fork_session(session_id, entity_type=entity_type)
        return AgentSessionRead.model_validate(forked, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
