"""Agent Session API router for unified session management.

This router consolidates chat and session endpoints into a unified /agent/sessions API.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

from tracecat import config
from tracecat.agent.adapter import vercel
from tracecat.agent.session.schemas import (
    AgentSessionCancelRequest,
    AgentSessionCancelResponse,
    AgentSessionCreate,
    AgentSessionForkRequest,
    AgentSessionRead,
    AgentSessionReadVercel,
    AgentSessionReadWithMessages,
    AgentSessionStatusRead,
    AgentSessionUpdate,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, AgentSessionStatus
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat, parse_vercel_frame_cursor
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.chat.schemas import (
    ChatRead,
    ChatReadMinimal,
    ChatReadVercel,
    ChatRequest,
    ContinueRunRequest,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(prefix="/agent/sessions", tags=["agent-sessions"])


def _bubble_id(session_id: uuid.UUID, curr_run_id: uuid.UUID | None) -> str | None:
    """Stable assistant-bubble id for a turn, if the turn is known."""
    return f"{session_id}:{curr_run_id}" if curr_run_id else None


def _redis_id_lt(a: str, b: str) -> bool:
    """Order Redis stream ids ("<ms>-<seq>") as (ms, seq) tuples."""

    def parts(rid: str) -> tuple[int, int]:
        ms, _, seq = rid.partition("-")
        return int(ms), int(seq or 0)

    return parts(a) < parts(b)


@router.post("")
@require_scope("agent:execute")
async def create_session(
    request: AgentSessionCreate,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> AgentSessionRead:
    """Create a new agent session associated with an entity."""
    svc = AgentSessionService(session, role)
    agent_session = await svc.create_session(request)
    return AgentSessionRead.model_validate(agent_session, from_attributes=True)


@router.get("")
@require_scope("agent:read")
async def list_sessions(
    role: WorkspaceUserRouteRole,
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
        config.TRACECAT__LIMIT_AGENT_SESSIONS_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum number of sessions to return",
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
@require_scope("agent:read")
async def get_session(
    session_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
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
            channel_context=agent_session.channel_context,
            tools=agent_session.tools,
            agent_preset_id=agent_session.agent_preset_id,
            agent_preset_version_id=agent_session.agent_preset_version_id,
            agents_binding=(
                ResolvedAgentsConfig.model_validate(agent_session.agents_binding)
                if agent_session.agents_binding is not None
                else None
            ),
            harness_type=agent_session.harness_type,
            created_at=agent_session.created_at,
            updated_at=agent_session.updated_at,
            last_stream_id=agent_session.last_stream_id,
            turn_status=AgentSessionStatus(agent_session.status),
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
            agent_preset_version_id=None,
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
@require_scope("agent:read")
async def get_session_vercel(
    session_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
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
            channel_context=agent_session.channel_context,
            tools=agent_session.tools,
            agent_preset_id=agent_session.agent_preset_id,
            agent_preset_version_id=agent_session.agent_preset_version_id,
            agents_binding=(
                ResolvedAgentsConfig.model_validate(agent_session.agents_binding)
                if agent_session.agents_binding is not None
                else None
            ),
            harness_type=agent_session.harness_type,
            created_at=agent_session.created_at,
            updated_at=agent_session.updated_at,
            last_stream_id=agent_session.last_stream_id,
            turn_status=AgentSessionStatus(agent_session.status),
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
            agent_preset_version_id=None,
            created_at=legacy_chat.created_at,
            updated_at=legacy_chat.updated_at,
            last_stream_id=legacy_chat.last_stream_id,
            messages=ui_messages,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Session not found",
    )


@router.get("/{session_id}/status")
@require_scope("agent:read")
async def get_session_status(
    session_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> AgentSessionStatusRead:
    """Cheap lifecycle status for polling (no message history loaded)."""
    svc = AgentSessionService(session, role)
    agent_session = await svc.get_session(session_id)
    if agent_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    turn_status = AgentSessionStatus(agent_session.status)
    prompt: str | None = None
    if (
        turn_status
        in {AgentSessionStatus.RUNNING, AgentSessionStatus.WAITING_FOR_APPROVAL}
        and agent_session.curr_run_id is not None
    ):
        prompt = await svc.get_active_run_prompt(session_id, agent_session.curr_run_id)
    return AgentSessionStatusRead(
        turn_status=turn_status,
        curr_run_id=agent_session.curr_run_id,
        prompt=prompt,
    )


@router.patch("/{session_id}")
@require_scope("agent:execute")
async def update_session(
    session_id: uuid.UUID,
    params: AgentSessionUpdate,
    role: WorkspaceUserRouteRole,
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
@require_scope("agent:execute")
async def delete_session(
    session_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
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


@router.post("/{session_id}/cancel")
@require_scope("agent:execute")
async def cancel_session(
    session_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    request: AgentSessionCancelRequest | None = None,
) -> AgentSessionCancelResponse:
    """Request graceful cancellation for the active agent session turn."""
    svc = AgentSessionService(session, role)
    try:
        return await svc.request_cancel(
            session_id,
            request or AgentSessionCancelRequest(),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.detail or str(e),
        ) from e


@router.post("/{session_id}/messages")
@require_scope("agent:execute")
async def send_message(
    session_id: uuid.UUID,
    request: ChatRequest,
    role: WorkspaceUserRouteRole,
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
        workspace_id = role.workspace_id
        if workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace access required",
            )

        stream = await AgentStream.new(session_id, workspace_id)
        async with AgentSessionService.with_session(role=role) as svc:
            # Check if this is a legacy chat (read-only)
            if await svc.is_legacy_session(session_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Legacy chat sessions are read-only and cannot receive new messages",
                )

            await svc.validate_turn_request(session_id=session_id, request=request)

            if isinstance(request, ContinueRunRequest):
                # Continuations should follow only newly appended events. Resuming
                # from the persisted DB cursor can replay the approval request that
                # the active client already rendered before clicking approve/deny.
                start_id = "$"
            else:
                # Each fresh execution turn gets a new Redis stream buffer so
                # stale events from the prior turn are never replayed.
                await stream.reset_for_new_turn()
                # Read from the beginning of the freshly cleared stream so we still
                # pick up events emitted before the SSE response starts consuming.
                start_id = "0-0"

            # Run session turn (spawns DurableAgentWorkflow)
            try:
                await svc.run_turn(
                    session_id=session_id,
                    request=request,
                )
            except Exception:
                if not isinstance(request, ContinueRunRequest):
                    try:
                        await stream.abort_new_turn()
                    except Exception as rollback_exc:
                        logger.warning(
                            "Failed to clear stream state after turn startup failure",
                            session_id=session_id,
                            error=str(rollback_exc),
                        )
                raise

            # run_turn set curr_run_id; build a bubble id stable for this turn.
            updated = await svc.get_session(session_id)
            message_id = _bubble_id(
                session_id, updated.curr_run_id if updated else None
            )

        logger.info(
            "Starting Vercel streaming session",
            session_id=session_id,
            start_id=start_id,
        )

        # Create stream and return with Vercel format
        return StreamingResponse(
            stream.sse(
                http_request.is_disconnected,
                last_id=start_id,
                format="vercel",
                message_id=message_id,
            ),
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
    except TracecatConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.detail or str(e),
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
@require_scope("agent:read")
async def stream_session_events(
    role: WorkspaceUserRouteRole,
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

    # Don't fail if the session doesn't exist yet: the frontend can connect
    # before the session row is created (handled by the 204 below).
    last_event_id = request.headers.get("Last-Event-ID")
    async with AgentSessionService.with_session(role=role) as svc:
        agent_session = await svc.get_session(session_id)
        curr_run_id = agent_session.curr_run_id if agent_session is not None else None
        if curr_run_id is None and last_event_id:
            curr_run_id = await svc.get_latest_history_run_id(session_id)

    is_stream_attachable = (
        agent_session is not None
        and agent_session.status == AgentSessionStatus.RUNNING.value
    )
    # Nothing live to attach to and no client cursor to resume -> let the client
    # fall back to the persisted DB history.
    if not is_stream_attachable and not last_event_id:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    stream = await AgentStream.new(session_id, workspace_id)
    message_id = _bubble_id(session_id, curr_run_id)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=120",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"

    # Browser owns the cursor: no Last-Event-ID -> replay 0-0; has one -> resume
    # after it. Readers never read last_stream_id (producer/lifecycle-only).
    start_id = "0-0"
    resume_from: str | None = None
    if last_event_id:
        cursor = parse_vercel_frame_cursor(last_event_id)
        requested = cursor.redis_id if cursor else last_event_id.split(":", 1)[0]
        min_id = await stream.min_entry_id()
        if min_id is None or _redis_id_lt(requested, min_id):
            # Cursor predates the live buffer (maxlen/TTL eviction). While running,
            # replay from the start. Otherwise there is nothing live, so emit a
            # finishing stream that ends cleanly -> client refetches DB history.
            if not is_stream_attachable:
                return StreamingResponse(
                    stream.finished_sse(format=format, message_id=message_id),
                    media_type="text/event-stream",
                    headers=headers,
                )
            # else start_id stays "0-0"
        else:
            start_id = requested
            resume_from = last_event_id if cursor else None

    # Terminal reconnects can outlive curr_run_id and, in edge cases, fail to
    # resolve a persisted run id. Do not invent a session-only assistant id:
    # finish cleanly so the client refetches DB history without rendering an
    # empty synthetic bubble.
    if not is_stream_attachable and message_id is None:
        return StreamingResponse(
            stream.finished_sse(format=format, message_id=None),
            media_type="text/event-stream",
            headers=headers,
        )

    logger.info(
        "Starting session stream",
        last_id=start_id,
        session_id=session_id,
    )

    return StreamingResponse(
        stream.sse(
            request.is_disconnected,
            last_id=start_id,
            format=format,
            message_id=message_id,
            resume_from=resume_from,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/{session_id}/fork")
@require_scope("agent:execute")
async def fork_session(
    session_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
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
