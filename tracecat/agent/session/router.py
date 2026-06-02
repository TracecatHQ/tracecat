"""Agent Session API router for unified session management.

This router consolidates chat and session endpoints into a unified /agent/sessions API.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from tracecat_ee.workspace_chat.policy import (
    is_workspace_chat_entitled,
    require_workspace_chat_entitlement_for_entity,
)

from tracecat import config
from tracecat.agent.adapter import vercel
from tracecat.agent.session.schemas import (
    AgentSessionArtifactsRead,
    AgentSessionCreate,
    AgentSessionForkRequest,
    AgentSessionRead,
    AgentSessionReadVercel,
    AgentSessionReadWithMessages,
    AgentSessionUpdate,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.stream.artifacts import artifact_stream_event
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.artifacts.bindings import ArtifactSideEffect
from tracecat.artifacts.schemas import ArtifactType
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.chat.schemas import (
    ChatRead,
    ChatReadMinimal,
    ChatReadVercel,
    ChatRequest,
    ContinueRunRequest,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import EntitlementRequired, TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(prefix="/agent/sessions", tags=["agent-sessions"])


async def _require_workspace_chat_entitlement_for_session_tree(
    *,
    svc: AgentSessionService,
    session: AsyncDBSession,
    role: WorkspaceActorRouteRole,
    agent_session: Any,
) -> None:
    """Require Workspace Chat access for a session and inherited parents."""
    seen: set[uuid.UUID] = set()
    current = agent_session
    while current is not None:
        current_id = getattr(current, "id", None)
        if isinstance(current_id, uuid.UUID):
            if current_id in seen:
                return
            seen.add(current_id)
        await require_workspace_chat_entitlement_for_entity(
            session=session,
            role=role,
            entity_type=AgentSessionEntity(current.entity_type),
        )
        parent_session_id = getattr(current, "parent_session_id", None)
        if parent_session_id is None:
            return
        current = await svc.get_session(parent_session_id)


@router.post("")
@require_scope("agent:execute")
async def create_session(
    request: AgentSessionCreate,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> AgentSessionRead:
    """Create a new agent session associated with an entity."""
    await require_workspace_chat_entitlement_for_entity(
        session=session,
        role=role,
        entity_type=request.entity_type,
    )
    svc = AgentSessionService(session, role)
    agent_session = await svc.create_session(request)
    return AgentSessionRead.model_validate(agent_session, from_attributes=True)


@router.get("")
@require_scope("agent:read")
async def list_sessions(
    role: WorkspaceActorRouteRole,
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
    if entity_type is AgentSessionEntity.WORKSPACE_CHAT:
        await require_workspace_chat_entitlement_for_entity(
            session=session,
            role=role,
            entity_type=entity_type,
        )
    elif not await is_workspace_chat_entitled(session, role):
        exclude_entity_types = [
            *(exclude_entity_types or []),
            AgentSessionEntity.WORKSPACE_CHAT,
        ]
    svc = AgentSessionService(session, role)
    return await svc.list_sessions(
        created_by=role.user_id,
        filter_created_by_none=role.type == "service_account",
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
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> AgentSessionReadWithMessages | ChatRead:
    """Get an agent session or legacy chat with its message history.

    Legacy chats have is_readonly=True.
    """
    svc = AgentSessionService(session, role)

    # Try AgentSession first
    agent_session = await svc.get_session(session_id)
    if agent_session:
        await _require_workspace_chat_entitlement_for_session_tree(
            svc=svc,
            session=session,
            role=role,
            agent_session=agent_session,
        )
        messages = await svc.list_messages(session_id)
        logger.info("Session read", session_id=agent_session.id, messages=len(messages))
        return AgentSessionReadWithMessages(
            id=agent_session.id,
            workspace_id=agent_session.workspace_id,
            title=agent_session.title,
            created_by=agent_session.created_by,
            entity_type=AgentSessionEntity(agent_session.entity_type),
            entity_id=agent_session.entity_id,
            channel_context=agent_session.channel_context,
            tools=agent_session.tools,
            mcp_integrations=agent_session.mcp_integrations,
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
            artifacts=svc.list_artifacts(agent_session),
            messages=messages,
        )

    # Try legacy Chat (user_id remains for legacy Chat model)
    legacy_chat = await svc.get_legacy_chat(session_id)
    if legacy_chat:
        await require_workspace_chat_entitlement_for_entity(
            session=session,
            role=role,
            entity_type=AgentSessionEntity(legacy_chat.entity_type),
        )
        messages = await svc.list_messages(session_id)
        logger.info(
            "Legacy chat read", session_id=legacy_chat.id, messages=len(messages)
        )
        return ChatRead(
            id=legacy_chat.id,
            title=legacy_chat.title,
            user_id=legacy_chat.user_id,
            entity_type=AgentSessionEntity(legacy_chat.entity_type),
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
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> AgentSessionReadVercel | ChatReadVercel:
    """Get an agent session or legacy chat with message history in Vercel format.

    Legacy chats have is_readonly=True.
    """
    svc = AgentSessionService(session, role)

    # Try AgentSession first
    agent_session = await svc.get_session(session_id)
    if agent_session:
        await _require_workspace_chat_entitlement_for_session_tree(
            svc=svc,
            session=session,
            role=role,
            agent_session=agent_session,
        )
        messages = await svc.list_messages(session_id)
        ui_messages = vercel.convert_chat_messages_to_ui(messages)
        return AgentSessionReadVercel(
            id=agent_session.id,
            workspace_id=agent_session.workspace_id,
            title=agent_session.title,
            created_by=agent_session.created_by,
            entity_type=AgentSessionEntity(agent_session.entity_type),
            entity_id=agent_session.entity_id,
            channel_context=agent_session.channel_context,
            tools=agent_session.tools,
            mcp_integrations=agent_session.mcp_integrations,
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
            artifacts=svc.list_artifacts(agent_session),
            messages=ui_messages,
        )

    # Try legacy Chat (user_id remains for legacy Chat model)
    legacy_chat = await svc.get_legacy_chat(session_id)
    if legacy_chat:
        await require_workspace_chat_entitlement_for_entity(
            session=session,
            role=role,
            entity_type=AgentSessionEntity(legacy_chat.entity_type),
        )
        messages = await svc.list_messages(session_id)
        ui_messages = vercel.convert_chat_messages_to_ui(messages)
        return ChatReadVercel(
            id=legacy_chat.id,
            title=legacy_chat.title,
            user_id=legacy_chat.user_id,
            entity_type=AgentSessionEntity(legacy_chat.entity_type),
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


@router.patch("/{session_id}")
@require_scope("agent:execute")
async def update_session(
    session_id: uuid.UUID,
    params: AgentSessionUpdate,
    role: WorkspaceActorRouteRole,
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

    await require_workspace_chat_entitlement_for_entity(
        session=session,
        role=role,
        entity_type=agent_session.entity_type,
    )

    updated = await svc.update_session(agent_session, params=params)
    return AgentSessionRead.model_validate(updated, from_attributes=True)


@router.delete("/{session_id}/artifacts/{artifact_type}/{artifact_id}")
@require_scope("agent:execute")
async def remove_session_artifact(
    session_id: uuid.UUID,
    artifact_type: ArtifactType,
    artifact_id: str,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> AgentSessionArtifactsRead:
    """Remove one artifact from a session's persisted artifact projection."""
    svc = AgentSessionService(session, role)

    if await svc.is_legacy_session(session_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Legacy chat sessions do not support artifacts",
        )

    try:
        agent_session = await svc.get_session(session_id)
        if agent_session is None:
            raise TracecatNotFoundError(f"Session {session_id} not found")
        await require_workspace_chat_entitlement_for_entity(
            session=session,
            role=role,
            entity_type=agent_session.entity_type,
        )
        artifacts = await svc.remove_artifact(
            session_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return AgentSessionArtifactsRead(artifacts=artifacts)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:execute")
async def delete_session(
    session_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
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

    await require_workspace_chat_entitlement_for_entity(
        session=session,
        role=role,
        entity_type=agent_session.entity_type,
    )

    await svc.delete_session(agent_session)


@router.post("/{session_id}/messages")
@require_scope("agent:execute")
async def send_message(
    session_id: uuid.UUID,
    request: ChatRequest,
    role: WorkspaceActorRouteRole,
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

            agent_session = await svc.validate_turn_request(
                session_id=session_id,
                request=request,
            )
            await _require_workspace_chat_entitlement_for_session_tree(
                svc=svc,
                session=svc.session,
                role=role,
                agent_session=agent_session,
            )

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
                if await svc.should_seed_initial_artifact(agent_session) and (
                    artifact := await svc.build_initial_artifact(agent_session)
                ):
                    await svc.apply_artifact_side_effects(
                        session_id,
                        [ArtifactSideEffect(op="upsert", artifact=artifact)],
                    )
                    await stream.append(artifact_stream_event("upsert", artifact))

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

        logger.info(
            "Starting Vercel streaming session",
            session_id=session_id,
            start_id=start_id,
        )

        # Create stream and return with Vercel format
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
    except EntitlementRequired:
        raise
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
    role: WorkspaceActorRouteRole,
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
            await _require_workspace_chat_entitlement_for_session_tree(
                svc=svc,
                session=svc.session,
                role=role,
                agent_session=agent_session,
            )
            last_stream_id = agent_session.last_stream_id
        else:
            legacy_chat = await svc.get_legacy_chat(session_id)
            if legacy_chat is not None:
                await require_workspace_chat_entitlement_for_entity(
                    session=svc.session,
                    role=role,
                    entity_type=AgentSessionEntity(legacy_chat.entity_type),
                )
                last_stream_id = legacy_chat.last_stream_id

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
@require_scope("agent:execute")
async def fork_session(
    session_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
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
        parent_session = await svc.get_session(session_id)
        if parent_session is None:
            raise TracecatNotFoundError(
                f"Parent session with ID {session_id} not found"
            )
        await _require_workspace_chat_entitlement_for_session_tree(
            svc=svc,
            session=session,
            role=role,
            agent_session=parent_session,
        )
        entity_type = request.entity_type if request else None
        if entity_type is None:
            entity_type = AgentSessionEntity(parent_session.entity_type)
        await require_workspace_chat_entitlement_for_entity(
            session=session,
            role=role,
            entity_type=entity_type,
        )
        forked = await svc.fork_session(session_id, entity_type=entity_type)
        return AgentSessionRead.model_validate(forked, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
