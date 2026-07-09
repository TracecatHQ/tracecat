"""Agent Session API router for unified session management.

This router consolidates chat and session endpoints into a unified /agent/sessions API.
"""

import uuid
from collections.abc import AsyncIterator
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
    AgentSessionCancelRequest,
    AgentSessionCancelResponse,
    AgentSessionCreate,
    AgentSessionForkRequest,
    AgentSessionRead,
    AgentSessionReadVercel,
    AgentSessionReadWithMessages,
    AgentSessionUpdate,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
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
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.logger import logger

router = APIRouter(prefix="/agent/sessions", tags=["agent-sessions"])

# SSE headers for Vercel AI SDK streaming responses from POST /messages.
_VERCEL_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Transfer-Encoding": "chunked",
    "Content-Encoding": "none",
    "Connection": "keep-alive",
    "Keep-Alive": "timeout=120",
    "Pragma": "no-cache",
    "X-Accel-Buffering": "no",  # Disable nginx buffering
    "x-vercel-ai-ui-message-stream": "v1",
}


async def _empty_stream_events() -> AsyncIterator[Any]:
    """Empty event source for an immediately-finished Vercel SSE response."""
    return
    yield  # pragma: no cover - establishes async generator


def _bubble_id(session_id: uuid.UUID, curr_run_id: uuid.UUID | None) -> str | None:
    """Stable assistant-bubble id for a turn, if the turn is known.

    ``session_id:curr_run_id`` is stable for the whole run, so the AI SDK upserts
    the live assistant in place across reconnects instead of spawning a duplicate
    bubble.
    """
    return f"{session_id}:{curr_run_id}" if curr_run_id else None


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
            last_error=agent_session.last_error,
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
            last_error=agent_session.last_error,
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

        message_id: str | None = None
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

            is_first_prompt: bool | None = None
            if isinstance(request, ContinueRunRequest):
                # Continuations rotate the per-turn stream: run_turn mints a fresh
                # stream id, pins it on the session row, and hands it to the
                # workflow so every post-approval event lands there. The fresh
                # stream contains exactly the suffix (reconciled tool results +
                # new events), never the prefix, so a "0-0" attach is safe
                # regardless of whether the old buffer was already TTL-evicted.
                turn_response = await svc.run_turn(
                    session_id=session_id,
                    request=request,
                    active_stream_id=None,
                )
                rotated_stream_id = (
                    turn_response.active_stream_id
                    if turn_response is not None
                    else None
                )

                # Build a bubble id stable for this turn. Rotated continuations
                # carry the run id returned by run_turn; no-op continuations fall
                # back to the run id from the already-loaded session row.
                run_id = (
                    turn_response.curr_run_id
                    if turn_response is not None
                    else agent_session.curr_run_id
                )
                message_id = _bubble_id(session_id, run_id)

                if rotated_stream_id is None:
                    # No-op continuation (duplicate submission / approvals already
                    # resolved): no stream was rotated. Do NOT attach any Redis
                    # stream - a "0-0" attach on the stale pre-approval buffer
                    # would replay the turn prefix on top of the now-DB-visible
                    # rows (the approval filter unhides them), duplicating the
                    # turn client-side. Return an immediately-finished stream so
                    # the client gets a clean finish and refetches DB history.
                    logger.info(
                        "No-op continuation; returning finished stream",
                        session_id=session_id,
                    )
                    return StreamingResponse(
                        vercel.sse_vercel(
                            _empty_stream_events(), message_id=message_id
                        ),
                        media_type="text/event-stream",
                        headers=_VERCEL_SSE_HEADERS,
                    )

                stream = await AgentStream.new(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    stream_id=rotated_stream_id,
                )
                start_id = "0-0"
            else:
                # New turn: mint the per-turn stream id at the HTTP layer (turn start)
                # so the seed artifact and the worker producer both write to the same
                # fresh per-turn key. No reset/reuse of a prior turn's buffer.
                stream_id = uuid.uuid4()
                stream = await AgentStream.new(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    stream_id=stream_id,
                )
                start_id = "0-0"
                is_first_prompt = await svc.is_first_prompt_for_session(session_id)
                if is_first_prompt and (
                    artifact := await svc.build_initial_artifact(agent_session)
                ):
                    await svc.apply_artifact_side_effects(
                        session_id,
                        [ArtifactSideEffect(op="upsert", artifact=artifact)],
                    )
                    await stream.append(artifact_stream_event("upsert", artifact))

                # Run session turn (spawns DurableAgentWorkflow)
                try:
                    turn_response = await svc.run_turn(
                        session_id=session_id,
                        request=request,
                        active_stream_id=stream_id,
                        is_first_prompt=is_first_prompt,
                    )
                except Exception as turn_exc:
                    if not isinstance(request, ContinueRunRequest):
                        # Startup failed after we minted the stream: surface a terminal
                        # frame so reconnecting clients don't hang, and clear pointers.
                        # Non-continue turns always mint a fresh per-turn stream id.
                        assert stream_id is not None
                        logger.warning(
                            "Failed to start agent turn",
                            session_id=session_id,
                            error=str(turn_exc),
                        )
                        try:
                            await stream.error(
                                f"Failed to start agent turn for session {session_id}"
                            )
                            await stream.done()
                            await svc.clear_active_turn(
                                session_id, expected_stream_id=stream_id
                            )
                        except Exception as rollback_exc:
                            logger.warning(
                                "Failed to clear stream state after turn startup failure",
                                session_id=session_id,
                                error=str(rollback_exc),
                            )
                    raise

                # Build a bubble id stable for this turn. Prefer the run id returned by
                # run_turn (new turns) — terminal cleanup may already have nulled the
                # session row on a fast turn. Continuations return None and reuse the
                # in-progress run id still pinned on the session row.
                if turn_response is not None and turn_response.curr_run_id is not None:
                    run_id = turn_response.curr_run_id
                else:
                    refreshed = await svc.get_session(session_id)
                    run_id = refreshed.curr_run_id if refreshed else None
                message_id = _bubble_id(session_id, run_id)

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
            headers=_VERCEL_SSE_HEADERS,
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

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=120",
        "Pragma": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    if format == "vercel":
        headers["x-vercel-ai-ui-message-stream"] = "v1"

    last_event_id = request.headers.get("Last-Event-ID")

    # Resolve the turn lifecycle. Temporal owns it: we describe the current run
    # live rather than reading a cached DB status. Don't fail if the session row
    # doesn't exist yet (the frontend may connect before it is created).
    async with AgentSessionService.with_session(role=role) as svc:
        agent_session = await svc.get_session(session_id)
        if agent_session is None:
            # Legacy chat fallback: no Temporal workflow / per-turn key. Keep the
            # old per-session behaviour driven by the stored cursor.
            legacy_chat = await svc.get_legacy_chat(session_id)
            if legacy_chat is None:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            await require_workspace_chat_entitlement_for_entity(
                session=svc.session,
                role=role,
                entity_type=AgentSessionEntity(legacy_chat.entity_type),
            )
            last_stream_id = legacy_chat.last_stream_id
            if last_stream_id is None and not last_event_id:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            start_id = last_event_id or last_stream_id or "0-0"
            legacy_stream = await AgentStream.new(
                session_id=session_id, workspace_id=workspace_id
            )
            return StreamingResponse(
                legacy_stream.sse(
                    request.is_disconnected, last_id=start_id, format=format
                ),
                media_type="text/event-stream",
                headers=headers,
            )

        await _require_workspace_chat_entitlement_for_session_tree(
            svc=svc,
            session=svc.session,
            role=role,
            agent_session=agent_session,
        )
        stream_state = await svc.get_stream_resume_state(agent_session)

    message_id = _bubble_id(session_id, stream_state.curr_run_id)

    # FAILED | TERMINATED (incl. failed-to-start) | CANCELLED: the workflow will
    # not produce a terminal frame, so emit one ourselves and let the client
    # refetch DB history.
    if stream_state.lifecycle in (TurnLifecycle.FAILED, TurnLifecycle.CANCELLED):
        finished = await AgentStream.new(
            session_id=session_id,
            workspace_id=workspace_id,
            stream_id=stream_state.active_stream_id,
        )
        return StreamingResponse(
            finished.finished_sse(format=format, message_id=message_id),
            media_type="text/event-stream",
            headers=headers,
        )

    # No live run, or the run is already COMPLETED: nothing to attach to. The
    # canonical assistant message is in DB history; the client refetches.
    if not stream_state.has_live_stream:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # RUNNING: join the per-turn Redis stream and always replay the whole active
    # turn from the start. The mid-turn DB load hides the active run's rows, so
    # Redis is the sole source for the live assistant; a partial (Last-Event-ID)
    # resume would drop everything before the cursor. Full 0-0 replay keeps the
    # bubble whole at the cost of re-streaming the in-flight turn on reconnect.
    # (Cursor/frame-precise resume is intentionally not used here; revisit if we
    # reconcile committed partial rows with the live stream id.)
    stream = await AgentStream.new(
        session_id=session_id,
        workspace_id=workspace_id,
        stream_id=stream_state.active_stream_id,
    )
    start_id = "0-0"
    resume_from: str | None = None

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


@router.post("/{session_id}/cancel")
@require_scope("agent:execute")
async def cancel_session(
    session_id: uuid.UUID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    request: AgentSessionCancelRequest | None = None,
) -> AgentSessionCancelResponse:
    """Request graceful cancellation for the active agent session turn."""
    svc = AgentSessionService(session, role)
    reason = request.reason if request else "user_cancel"
    try:
        agent_session = await svc.get_session(session_id)
        if agent_session is None:
            raise TracecatNotFoundError(f"Session with ID {session_id} not found")
        await _require_workspace_chat_entitlement_for_session_tree(
            svc=svc,
            session=session,
            role=role,
            agent_session=agent_session,
        )
        return await svc.request_cancel(session_id, reason=reason)
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
