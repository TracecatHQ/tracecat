"""Agent Session API router."""

import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

from tracecat import config
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
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.chat.schemas import ChatRequest, ContinueRunRequest
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(prefix="/agent/sessions", tags=["agent-sessions"])


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
) -> list[AgentSessionRead]:
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
) -> AgentSessionReadWithMessages:
    """Get an agent session with its message history."""
    svc = AgentSessionService(session, role)

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
) -> AgentSessionReadVercel:
    """Get an agent session with message history in Vercel UI format."""
    svc = AgentSessionService(session, role)

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
            messages=ui_messages,
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Session not found",
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

    agent_session = await svc.get_session(session_id)
    if not agent_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await svc.delete_session(agent_session)


@router.post("/{session_id}/messages")
@require_scope("agent:execute")
async def send_message(
    session_id: uuid.UUID,
    request: ChatRequest,
    role: WorkspaceUserRouteRole,
    http_request: Request,
) -> StreamingResponse:
    """Send a message to the agent session and stream the response.

    Compatible with Vercel's AI SDK useChat hook. Starts agent execution
    and streams the response in Vercel's data protocol format.
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
            await svc.validate_turn_request(session_id=session_id, request=request)

            if isinstance(request, ContinueRunRequest):
                # Continuations follow only newly appended events to avoid
                # replaying the approval request the client already rendered.
                start_id = "$"
            else:
                # Fresh turns get a new Redis stream buffer so stale events
                # from the prior turn are never replayed.
                await stream.reset_for_new_turn()
                start_id = "0-0"

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

        logger.info(
            "Starting Vercel streaming session",
            session_id=session_id,
            start_id=start_id,
        )

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
                "X-Accel-Buffering": "no",
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
@require_scope("agent:read")
async def stream_session_events(
    role: WorkspaceUserRouteRole,
    request: Request,
    session_id: uuid.UUID,
    format: StreamFormat = Query(
        default="vercel", description="Streaming format (e.g. 'vercel')"
    ),
):
    """Stream session events via Server-Sent Events."""
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    last_stream_id: str | None = None
    async with AgentSessionService.with_session(role=role) as svc:
        agent_session = await svc.get_session(session_id)
        if agent_session is not None:
            last_stream_id = agent_session.last_stream_id

    last_event_id = request.headers.get("Last-Event-ID")
    if last_stream_id is None and not last_event_id:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    start_id = last_event_id or last_stream_id or "0-0"
    logger.info(
        "Starting session stream",
        last_id=start_id,
        session_id=session_id,
    )

    stream = await AgentStream.new(session_id, workspace_id)
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=120",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"
    return StreamingResponse(
        stream.sse(request.is_disconnected, last_id=start_id, format=format),
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
    """Fork an existing session to continue conversation post-decision."""
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
