from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast, get_args
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette import status

from tracecat.agent.adapter.vercel import UIMessage
from tracecat.agent.backends import registry
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.types import (
    AgentControlRejected,
    AgentControlUncertain,
    SessionDispatchUncertain,
)
from tracecat.agent.common.stream_types import (
    HarnessType,
    StreamEventType,
    UnifiedStreamEvent,
)
from tracecat.agent.control.schemas import (
    AgentCancelControl,
    AgentControlReceipt,
    AgentSteerControl,
    AgentWithdrawControl,
)
from tracecat.agent.session.router import (
    cancel_session,
    control_session,
    create_session,
    fork_session,
    get_session,
    get_session_vercel,
    list_sessions,
    remove_session_artifact,
    router,
    send_message,
    stream_session_events,
    update_session,
)
from tracecat.agent.session.schemas import (
    AgentSessionCancelRequest,
    AgentSessionCreate,
    AgentSessionForkRequest,
    AgentSessionUpdate,
)
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.session.views import build_session_read
from tracecat.artifacts.schemas import CaseArtifact
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus
from tracecat.chat.schemas import (
    ApprovalDecision,
    ChatResponse,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.db.engine import get_async_session
from tracecat.exceptions import (
    AgentProviderCredentialsNotFoundError,
    EntitlementRequired,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatServiceError,
    TracecatValidationError,
)


async def _empty_event_stream() -> AsyncIterator[None]:
    if False:
        yield


def _agent_session_stub(**overrides: Any) -> SimpleNamespace:
    now = overrides.pop("now", None)
    if now is None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)

    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "title": "New Chat",
        "created_by": uuid.uuid4(),
        "entity_type": AgentSessionEntity.AGENT_PRESET,
        "entity_id": uuid.uuid4(),
        "channel_context": None,
        "tools": None,
        "mcp_integrations": None,
        "agent_preset_id": uuid.uuid4(),
        "agent_preset_version_id": uuid.uuid4(),
        "agents_binding": {},
        "backend_id": "oss",
        "harness_type": HarnessType.CLAUDE_CODE,
        "created_at": now,
        "updated_at": now,
        "last_stream_id": None,
        "active_stream_id": None,
        "curr_run_id": None,
        "last_error": None,
        "artifacts": [],
        "parent_session_id": None,
        "spawned_by_session_id": None,
        "forked_from_session_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _read_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


def _service_account_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service_account",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        bound_workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


async def _deny_workspace_chat_entitlement(**kwargs: Any) -> None:
    if AgentSessionEntity(kwargs["entity_type"]) == AgentSessionEntity.WORKSPACE_CHAT:
        raise EntitlementRequired("workspace_chat")


def _without_workspace_chat_entitlement() -> Any:
    """Patch the org off workspace chat, which OSS defaults now grant."""
    return patch(
        "tracecat.agent.session.router.is_workspace_chat_entitled",
        AsyncMock(return_value=False),
    )


@pytest.mark.parametrize(
    ("filter_names", "conflicting", "expected_status"),
    [
        ((), False, 200),
        (("parent_session_id",), False, 200),
        (("forked_from_session_id",), False, 200),
        (("parent_session_id", "forked_from_session_id"), False, 200),
        (("parent_session_id", "forked_from_session_id"), True, 400),
    ],
)
def test_list_sessions_http_fork_filter_compatibility(
    filter_names: tuple[str, ...], conflicting: bool, expected_status: int
) -> None:
    """Old and new Inbox requests must retain their history-source filter."""
    role = _read_role(uuid.uuid4())
    source_id = uuid.uuid4()
    user_id = uuid.uuid4()
    params = {"created_by": str(user_id), "limit": "1"}
    params.update({name: str(source_id) for name in filter_names})
    if conflicting:
        params["forked_from_session_id"] = str(uuid.uuid4())

    app = FastAPI()
    app.include_router(router)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: role
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    fake_svc = SimpleNamespace(list_sessions=AsyncMock(return_value=[]))
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        _without_workspace_chat_entitlement(),
        TestClient(app) as client,
    ):
        response = client.get("/agent/sessions", params=params)

    assert response.status_code == expected_status
    if conflicting:
        fake_svc.list_sessions.assert_not_awaited()
        return
    fake_svc.list_sessions.assert_awaited_once_with(
        created_by=user_id,
        entity_type=None,
        entity_id=None,
        exclude_entity_types=None,
        spawned_by_session_id=None,
        forked_from_session_id=source_id if filter_names else None,
        include_children=False,
        limit=1,
    )


@pytest.mark.anyio
async def test_list_sessions_service_account_defaults_to_workspace_sessions() -> None:
    workspace_id = uuid.uuid4()
    role = _service_account_role(workspace_id)
    fake_svc = SimpleNamespace(list_sessions=AsyncMock(return_value=[]))

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        _without_workspace_chat_entitlement(),
    ):
        raw_list_sessions = cast(Any, list_sessions).__wrapped__
        response = await raw_list_sessions(
            role=role,
            session=AsyncMock(),
            entity_type=None,
            entity_id=None,
            created_by=None,
            exclude_entity_types=None,
            spawned_by_session_id=None,
            forked_from_session_id=None,
            include_children=False,
            limit=100,
        )

    assert response == []
    fake_svc.list_sessions.assert_awaited_once_with(
        created_by=None,
        entity_type=None,
        entity_id=None,
        exclude_entity_types=None,
        spawned_by_session_id=None,
        forked_from_session_id=None,
        include_children=False,
        limit=100,
    )


@pytest.mark.anyio
async def test_list_sessions_user_defaults_to_workspace_sessions() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )
    fake_svc = SimpleNamespace(list_sessions=AsyncMock(return_value=[]))

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        _without_workspace_chat_entitlement(),
    ):
        raw_list_sessions = cast(Any, list_sessions).__wrapped__
        response = await raw_list_sessions(
            role=role,
            session=AsyncMock(),
            entity_type=None,
            entity_id=None,
            created_by=None,
            exclude_entity_types=None,
            spawned_by_session_id=None,
            forked_from_session_id=None,
            include_children=False,
            limit=100,
        )

    assert response == []
    fake_svc.list_sessions.assert_awaited_once_with(
        created_by=None,
        entity_type=None,
        entity_id=None,
        exclude_entity_types=None,
        spawned_by_session_id=None,
        forked_from_session_id=None,
        include_children=False,
        limit=100,
    )


@pytest.mark.anyio
async def test_update_session_rejects_teammate_session() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    session_stub = _agent_session_stub(
        workspace_id=workspace_id,
        created_by=uuid.uuid4(),
    )
    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        get_session=AsyncMock(return_value=session_stub),
        update_session=AsyncMock(),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_update_session = cast(Any, update_session).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_update_session(
                session_id=session_stub.id,
                params=AgentSessionUpdate(title="Nope"),
                role=role,
                session=AsyncMock(),
            )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == {
        "code": "session_read_only",
        "message": "Teammate sessions are read-only.",
    }
    fake_svc.update_session.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", [get_session, get_session_vercel])
async def test_get_session_includes_agents_binding(endpoint: Any) -> None:
    session_stub = _agent_session_stub()
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(return_value=[]),
        build_initial_artifact=AsyncMock(return_value=None),
        list_artifacts=Mock(return_value=[]),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        response = await endpoint.__wrapped__(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    assert response.model_dump(mode="json")["agents_binding"] == {
        "enabled": True,
        "subagents": [],
    }


@pytest.mark.anyio
async def test_get_session_vercel_includes_persisted_artifacts() -> None:
    session_stub = _agent_session_stub()
    artifact = CaseArtifact(
        id=str(session_stub.entity_id),
        title="Investigate suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )
    session_stub.artifacts = [artifact.model_dump(mode="json")]
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(return_value=[]),
        list_artifacts=Mock(return_value=[artifact]),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_get_session_vercel = cast(Any, get_session_vercel).__wrapped__
        response = await raw_get_session_vercel(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    payload = response.model_dump(mode="json")
    assert payload["messages"] == []
    assert payload["artifacts"] == [
        {
            "type": "case",
            "id": str(session_stub.entity_id),
            "title": "Investigate suspicious login",
            "scope": None,
            "severity": "high",
            "status": "new",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", [get_session, get_session_vercel])
@pytest.mark.parametrize("related", [False, True])
async def test_session_history_remains_readable_without_entitlement(
    endpoint: Any, related: bool
) -> None:
    session_stub = _agent_session_stub(
        entity_type=(
            AgentSessionEntity.APPROVAL
            if related
            else AgentSessionEntity.WORKSPACE_CHAT
        ),
        forked_from_session_id=uuid.uuid4() if related else None,
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        get_workspace_chat_session_ids=AsyncMock(return_value={session_stub.id}),
        list_messages=AsyncMock(return_value=[]),
    )
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        _without_workspace_chat_entitlement(),
    ):
        response = await endpoint.__wrapped__(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )
    assert response.is_readonly is True
    fake_svc.list_messages.assert_awaited_once_with(session_stub.id)
    fake_svc.get_workspace_chat_session_ids.assert_awaited_once_with([session_stub.id])


@pytest.mark.anyio
async def test_remove_session_artifact_removes_and_returns_artifacts() -> None:
    session_stub = _agent_session_stub()
    artifact = CaseArtifact(
        id="case-2",
        title="Investigate suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )
    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        get_session=AsyncMock(return_value=session_stub),
        remove_artifact=AsyncMock(return_value=[artifact]),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_remove_session_artifact = cast(Any, remove_session_artifact).__wrapped__
        response = await raw_remove_session_artifact(
            session_id=session_stub.id,
            artifact_type="case",
            artifact_id="case-1",
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    fake_svc.remove_artifact.assert_awaited_once_with(
        session_stub.id,
        artifact_type="case",
        artifact_id="case-1",
    )
    assert response.model_dump(mode="json") == {
        "artifacts": [
            {
                "type": "case",
                "id": "case-2",
                "title": "Investigate suspicious login",
                "scope": None,
                "severity": "high",
                "status": "new",
            }
        ]
    }


def _execute_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


def _fake_stream() -> SimpleNamespace:
    return SimpleNamespace(
        error=AsyncMock(return_value=None),
        done=AsyncMock(return_value=None),
        append=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )


async def _call_send_message(
    fake_svc: SimpleNamespace,
    fake_stream: SimpleNamespace,
    *,
    session_id: uuid.UUID,
    request: Any,
    role: Role,
) -> tuple[Any, AsyncMock]:
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ) as with_session_mock,
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ) as stream_new_mock,
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        response = await raw_send_message(
            session_id=session_id,
            request=request,
            role=role,
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )
    with_session_mock.assert_called_once_with(role=role)
    return response, stream_new_mock


@pytest.mark.anyio
@pytest.mark.parametrize("rotated", [True, False])
async def test_send_message_continue_attaches_rotated_stream_or_finishes(
    rotated: bool,
) -> None:
    """Continuation attaches the stream id run_turn rotated to.

    A no-op continuation (run_turn returns None) never attaches Redis: replaying
    the stale pre-approval buffer at 0-0 would duplicate the turn prefix, so the
    router returns an immediately-finished SSE response instead.
    """
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    run_id = uuid.uuid4()
    rotated_stream_id = uuid.uuid4()
    agent_session = _agent_session_stub(
        id=session_id,
        workspace_id=workspace_id,
        active_stream_id=uuid.uuid4(),
        curr_run_id=run_id,
    )
    role = _execute_role(workspace_id)
    request = ContinueRunRequest(
        decisions=[ApprovalDecision(tool_call_id="tool_call_123", action="approve")],
        source="inbox",
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=agent_session),
        get_session=AsyncMock(return_value=agent_session),
        run_turn=AsyncMock(
            return_value=ChatResponse(
                stream_url="/stream",
                chat_id=session_id,
                active_stream_id=rotated_stream_id,
                curr_run_id=run_id,
            )
            if rotated
            else None
        ),
        build_initial_artifact=AsyncMock(return_value=None),
    )
    fake_stream = _fake_stream()

    response, stream_new_mock = await _call_send_message(
        fake_svc, fake_stream, session_id=session_id, request=request, role=role
    )

    assert isinstance(response, StreamingResponse)
    fake_svc.validate_turn_request.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )
    fake_svc.get_session.assert_not_awaited()
    # run_turn is called without a pre-minted id; it rotates internally.
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
        active_stream_id=None,
    )
    if not rotated:
        stream_new_mock.assert_not_awaited()
        frames = [chunk async for chunk in response.body_iterator]
        assert "data:" in "".join(str(f) for f in frames)
        return
    stream_new_mock.assert_awaited_once_with(
        session_id=session_id,
        workspace_id=workspace_id,
        stream_id=rotated_stream_id,
    )
    fake_stream.sse.assert_called_once()
    # Fresh stream is suffix-only, so attach from the start (0-0), not "$".
    assert fake_stream.sse.call_args.kwargs["last_id"] == "0-0"


@pytest.mark.anyio
@pytest.mark.parametrize("is_first_prompt", [False, True])
async def test_send_message_new_turn_uses_fresh_per_turn_stream(
    is_first_prompt: bool,
) -> None:
    """A new turn mints one stream id for the stream key and run_turn.

    The bubble id comes from the run id run_turn returns, not the re-read session
    row, because a fast finalize_turn may already have cleared curr_run_id. Only
    the first prompt appends the initial entity artifact.
    """
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    run_id = uuid.uuid4()
    agent_session = _agent_session_stub(
        id=session_id, workspace_id=workspace_id, curr_run_id=None
    )
    artifact = CaseArtifact(
        id=str(agent_session.entity_id),
        title="Investigate suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )
    role = _execute_role(workspace_id)
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-1",
            role="user",
            parts=[{"type": "text", "text": "hello"}],
        ),
        model="gpt-4o-mini",
        model_provider="openai",
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=agent_session),
        get_session=AsyncMock(return_value=agent_session),
        run_turn=AsyncMock(
            return_value=ChatResponse(
                stream_url="/stream",
                chat_id=session_id,
                curr_run_id=run_id,
            )
        ),
        is_first_prompt_for_session=AsyncMock(return_value=is_first_prompt),
        build_initial_artifact=AsyncMock(return_value=artifact),
        apply_artifact_side_effects=AsyncMock(return_value=[artifact]),
    )
    fake_stream = _fake_stream()

    response, stream_new_mock = await _call_send_message(
        fake_svc, fake_stream, session_id=session_id, request=request, role=role
    )

    assert isinstance(response, StreamingResponse)
    fake_svc.is_first_prompt_for_session.assert_awaited_once_with(session_id)
    fake_stream.sse.assert_called_once()
    assert fake_stream.sse.call_args.kwargs["last_id"] == "0-0"
    assert fake_stream.sse.call_args.kwargs["message_id"] == f"{session_id}:{run_id}"
    await_args = stream_new_mock.await_args
    assert await_args is not None
    new_call_kwargs = await_args.kwargs
    assert new_call_kwargs["session_id"] == session_id
    assert new_call_kwargs["workspace_id"] == workspace_id
    minted_stream_id = new_call_kwargs["stream_id"]
    assert isinstance(minted_stream_id, uuid.UUID)
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
        active_stream_id=minted_stream_id,
        is_first_prompt=is_first_prompt,
    )
    if not is_first_prompt:
        fake_svc.build_initial_artifact.assert_not_awaited()
        fake_svc.apply_artifact_side_effects.assert_not_awaited()
        fake_stream.append.assert_not_awaited()
        return
    fake_svc.build_initial_artifact.assert_awaited_once_with(agent_session)
    fake_stream.append.assert_awaited_once()
    artifact_event = fake_stream.append.await_args.args[0]
    assert isinstance(artifact_event, UnifiedStreamEvent)
    assert artifact_event.type is StreamEventType.ARTIFACT
    assert artifact_event.artifact_data is not None
    assert artifact_event.artifact_data.op == "upsert"
    assert artifact_event.artifact_data.artifact == {
        "type": "case",
        "id": str(agent_session.entity_id),
        "title": "Investigate suspicious login",
        "severity": "high",
        "status": "new",
    }
    apply_args = fake_svc.apply_artifact_side_effects.await_args.args
    assert apply_args[0] == session_id
    assert len(apply_args[1]) == 1
    assert apply_args[1][0].op == "upsert"
    assert apply_args[1][0].artifact == artifact


@pytest.mark.anyio
@pytest.mark.parametrize("uncertain", [False, True])
async def test_send_message_new_turn_preserves_only_uncertain_dispatch(
    uncertain: bool,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    agent_session = _agent_session_stub(id=session_id, workspace_id=workspace_id)
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-1",
            role="user",
            parts=[{"type": "text", "text": "hello"}],
        ),
        model="gpt-4o-mini",
        model_provider="openai",
    )

    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=agent_session),
        get_session=AsyncMock(return_value=agent_session),
        run_turn=AsyncMock(
            side_effect=SessionDispatchUncertain("uncertain")
            if uncertain
            else RuntimeError("temporal unavailable")
        ),
        is_first_prompt_for_session=AsyncMock(return_value=False),
        build_initial_artifact=AsyncMock(return_value=None),
        clear_active_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        error=AsyncMock(return_value=None),
        done=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == 500
    fake_svc.is_first_prompt_for_session.assert_awaited_once_with(session_id)
    fake_svc.build_initial_artifact.assert_not_awaited()
    fake_svc.run_turn.assert_awaited_once()
    if uncertain:
        # A lost start acknowledgement must not truncate a possibly live reply.
        fake_stream.error.assert_not_awaited()
        fake_stream.done.assert_not_awaited()
        fake_svc.clear_active_turn.assert_not_awaited()
    else:
        fake_stream.error.assert_awaited_once()
        fake_stream.done.assert_awaited_once()
        fake_svc.clear_active_turn.assert_awaited_once()
        clear_call = fake_svc.clear_active_turn.await_args
        assert clear_call.args == (session_id,)
        assert isinstance(clear_call.kwargs["expected_stream_id"], uuid.UUID)
    fake_stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_send_message_maps_conflicting_decision_to_409() -> None:
    """A decision contradicting a recorded one is a conflict, not a 500."""
    session_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = ContinueRunRequest(
        decisions=[ApprovalDecision(tool_call_id="tool_call_123", action="deny")],
        source="slack",
    )

    fake_svc = SimpleNamespace(
        session=Mock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(
            return_value=SimpleNamespace(curr_run_id=None, created_by=None)
        ),
        run_turn=AsyncMock(
            side_effect=TracecatConflictError(
                "Approval decision conflicts with a decision already recorded"
                " for tool call tool_call_123"
            )
        ),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router._require_workspace_chat_entitlement_for_session_tree",
            AsyncMock(return_value=None),
        ),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == 409
    assert "already recorded" in str(exc_info.value.detail)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (
            AgentProviderCredentialsNotFoundError(
                "No credentials found for provider 'example'. "
                "Please configure credentials for this provider first."
            ),
            status.HTTP_409_CONFLICT,
        ),
        (TracecatNotFoundError("Session not found"), status.HTTP_404_NOT_FOUND),
    ],
)
async def test_send_message_maps_missing_provider_credentials_to_409(
    exc: TracecatNotFoundError, expected_status: int
) -> None:
    """Missing provider credentials are a 409 with the message; unknown stays 404."""
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    agent_session = _agent_session_stub(id=session_id, workspace_id=workspace_id)
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-1",
            role="user",
            parts=[{"type": "text", "text": "hello"}],
        ),
        model="gemini-2.5-flash",
        model_provider="gemini",
    )

    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=agent_session),
        get_session=AsyncMock(return_value=agent_session),
        run_turn=AsyncMock(side_effect=exc),
        is_first_prompt_for_session=AsyncMock(return_value=False),
        build_initial_artifact=AsyncMock(return_value=None),
        clear_active_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        error=AsyncMock(return_value=None),
        done=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == str(exc)
    # Nothing runs: the turn's stream and reservation are rolled back.
    fake_svc.clear_active_turn.assert_awaited_once()
    fake_stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_send_message_rejects_teammate_session() -> None:
    session_id = uuid.uuid4()
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = ContinueRunRequest(
        decisions=[ApprovalDecision(tool_call_id="tool_call_123", action="deny")],
        source="inbox",
    )
    fake_svc = SimpleNamespace(
        session=Mock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(
            return_value=SimpleNamespace(
                curr_run_id=None,
                created_by=uuid.uuid4(),
            )
        ),
        run_turn=AsyncMock(),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService.with_session",
        return_value=_AsyncContext(fake_svc),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == {
        "code": "session_read_only",
        "message": "Teammate sessions are read-only.",
    }
    fake_svc.run_turn.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (TracecatNotFoundError("No active workflow run"), 404),
        (TracecatValidationError("Subagent preset uses manual approvals"), 400),
    ],
)
async def test_send_message_does_not_reset_stream_when_validation_fails(
    error: Exception, status_code: int
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(side_effect=error),
        run_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        error=AsyncMock(return_value=None),
        done=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ) as with_session_mock,
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ) as stream_new_mock,
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == str(error)
    with_session_mock.assert_called_once_with(role=role)
    # The stream is created only after validation passes, so a validation
    # failure never mints a stream.
    stream_new_mock.assert_not_awaited()
    fake_stream.sse.assert_not_called()
    fake_svc.run_turn.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "relationship", ["spawned_by_session_id", "forked_from_session_id"]
)
async def test_send_message_requires_entitlement_for_workspace_chat_parent(
    relationship: str,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    parent_session = _agent_session_stub(
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT,
    )
    child_session = _agent_session_stub(
        id=session_id,
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.APPROVAL,
        **{relationship: parent_session.id},
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=child_session),
        get_session=AsyncMock(return_value=parent_session),
        run_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        error=AsyncMock(return_value=None),
        done=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(side_effect=_deny_workspace_chat_entitlement),
        ),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(EntitlementRequired):
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    fake_svc.run_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_fork_session_requires_entitlement_for_workspace_chat_parent() -> None:
    parent_session = _agent_session_stub(entity_type=AgentSessionEntity.WORKSPACE_CHAT)
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=parent_session.workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=parent_session),
        fork_session=AsyncMock(return_value=None),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(side_effect=_deny_workspace_chat_entitlement),
        ),
    ):
        raw_fork_session = cast(Any, fork_session).__wrapped__
        with pytest.raises(EntitlementRequired):
            await raw_fork_session(
                session_id=parent_session.id,
                role=role,
                session=AsyncMock(),
                request=AgentSessionForkRequest(
                    entity_type=AgentSessionEntity.APPROVAL,
                ),
            )

    fake_svc.fork_session.assert_not_awaited()


@pytest.mark.anyio
async def test_fork_session_maps_backend_rejection_to_bad_request() -> None:
    source_session = _agent_session_stub(entity_type=AgentSessionEntity.CASE)
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=source_session.workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=source_session),
        fork_session=AsyncMock(
            side_effect=TracecatValidationError("Pi sessions do not support forks yet")
        ),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(return_value=None),
        ),
    ):
        raw_fork_session = cast(Any, fork_session).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_fork_session(
                session_id=source_session.id,
                role=role,
                session=AsyncMock(),
                request=None,
            )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Pi sessions do not support forks yet"


@pytest.mark.anyio
async def test_cancel_session_requires_entitlement_for_workspace_chat_parent() -> None:
    agent_session = _agent_session_stub(
        entity_type=AgentSessionEntity.WORKSPACE_CHAT,
        curr_run_id=uuid.uuid4(),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=agent_session.workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=agent_session),
        request_cancel=AsyncMock(return_value=None),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(side_effect=_deny_workspace_chat_entitlement),
        ),
    ):
        raw_cancel_session = cast(Any, cancel_session).__wrapped__
        with pytest.raises(EntitlementRequired):
            await raw_cancel_session(
                session_id=agent_session.id,
                role=role,
                session=AsyncMock(),
                request=AgentSessionCancelRequest(),
            )

    fake_svc.request_cancel.assert_not_awaited()


def _make_stream_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


def _stream_state(
    *,
    lifecycle: TurnLifecycle,
    curr_run_id: uuid.UUID | None,
    active_stream_id: uuid.UUID | None,
    has_live_stream: bool,
) -> SimpleNamespace:
    return SimpleNamespace(
        lifecycle=lifecycle,
        curr_run_id=curr_run_id,
        active_stream_id=active_stream_id,
        has_live_stream=has_live_stream,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("lifecycle", "has_live_stream", "headers", "expected"),
    [
        pytest.param(TurnLifecycle.NONE, False, {}, "no_content", id="no_turn"),
        pytest.param(
            TurnLifecycle.COMPLETED,
            False,
            {"Last-Event-ID": "1-0"},
            "no_content",
            id="completed",
        ),
        pytest.param(
            TurnLifecycle.RUNNING, False, {}, "no_content", id="pending_approvals"
        ),
        pytest.param(TurnLifecycle.FAILED, False, {}, "finished", id="failed"),
        pytest.param(TurnLifecycle.RUNNING, True, {}, "replay", id="running"),
        pytest.param(
            TurnLifecycle.RUNNING,
            True,
            {"Last-Event-ID": "1234-0:0"},
            "replay",
            id="running_ignores_cursor",
        ),
    ],
)
async def test_stream_session_events_lifecycle(
    lifecycle: TurnLifecycle,
    has_live_stream: bool,
    headers: dict[str, str],
    expected: str,
) -> None:
    """Map the turn lifecycle to the stream response.

    - No turn, a completed turn, or a turn paused on approvals (no live stream)
      returns 204 so the client renders DB history.
    - A failed turn emits a finishing stream so clients don't hang.
    - A running turn always replays from 0-0, ignoring Last-Event-ID: the
      mid-turn DB load hides the active run's rows, so a partial cursor resume
      would drop everything before the cursor.
    """
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)
    run_id = None if lifecycle is TurnLifecycle.NONE else uuid.uuid4()
    stream_id = (
        None
        if lifecycle in (TurnLifecycle.NONE, TurnLifecycle.COMPLETED)
        else uuid.uuid4()
    )
    fake_session = SimpleNamespace(
        id=session_id,
        parent_session_id=None,
        spawned_by_session_id=None,
        forked_from_session_id=None,
        entity_type=AgentSessionEntity.AGENT_PRESET,
        last_stream_id=None,
        active_stream_id=stream_id,
        curr_run_id=run_id,
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        get_session=AsyncMock(return_value=fake_session),
        get_stream_resume_state=AsyncMock(
            return_value=_stream_state(
                lifecycle=lifecycle,
                curr_run_id=run_id,
                active_stream_id=stream_id,
                has_live_stream=has_live_stream,
            )
        ),
    )
    fake_stream = SimpleNamespace(sse=Mock(return_value=_empty_event_stream()))
    finished_sse = Mock(return_value=_empty_event_stream())

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ) as stream_new_mock,
        patch(
            "tracecat.agent.session.router.AgentStream.finished_sse",
            finished_sse,
        ),
    ):
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(
                headers=headers, is_disconnected=AsyncMock(return_value=False)
            ),
            session_id=session_id,
            format="vercel",
        )

    fake_svc.get_stream_resume_state.assert_awaited_once_with(fake_session)
    if expected == "no_content":
        assert isinstance(response, Response)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        fake_stream.sse.assert_not_called()
        finished_sse.assert_not_called()
    elif expected == "finished":
        assert isinstance(response, StreamingResponse)
        finished_sse.assert_called_once_with(
            format="vercel", message_id=f"{session_id}:{run_id}"
        )
        fake_stream.sse.assert_not_called()
    else:
        assert isinstance(response, StreamingResponse)
        fake_stream.sse.assert_called_once()
        assert fake_stream.sse.call_args.kwargs["last_id"] == "0-0"
        assert fake_stream.sse.call_args.kwargs["resume_from"] is None
        assert (
            fake_stream.sse.call_args.kwargs["message_id"] == f"{session_id}:{run_id}"
        )
        stream_new_mock.assert_awaited_with(
            session_id=session_id, workspace_id=workspace_id, stream_id=stream_id
        )


@pytest.mark.anyio
async def test_stream_session_events_reads_legacy_workspace_chat_without_entitlement() -> (
    None
):
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    legacy_chat = SimpleNamespace(
        entity_type=AgentSessionEntity.WORKSPACE_CHAT,
        last_stream_id="1234-0",
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        get_session=AsyncMock(return_value=None),
        get_legacy_chat=AsyncMock(return_value=legacy_chat),
    )
    fake_stream = SimpleNamespace(sse=Mock(return_value=_empty_event_stream()))

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(side_effect=_deny_workspace_chat_entitlement),
        ),
    ):
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(
                headers={"Last-Event-ID": "1234-0"},
                is_disconnected=AsyncMock(return_value=False),
            ),
            session_id=session_id,
        )

    assert isinstance(response, StreamingResponse)
    fake_stream.sse.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", [get_session, get_session_vercel])
async def test_session_read_surfaces_history_failure(endpoint) -> None:
    session_stub = _agent_session_stub(
        backend_id="uninstalled", harness_type="custom_harness"
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(
            side_effect=TracecatServiceError(
                "Cannot read session history because its backend is not installed"
            )
        ),
        list_artifacts=Mock(return_value=[]),
    )
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        pytest.raises(TracecatServiceError, match="backend is not installed"),
    ):
        await cast(Any, endpoint).__wrapped__(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("backend_state", ["missing", "disabled"])
async def test_create_session_rejects_unavailable_backend_with_bad_request(
    backend_state: str,
) -> None:
    db = AsyncMock()
    provider = DefaultBackend()
    with (
        patch.object(
            registry,
            "get_agent_backends",
            return_value={} if backend_state == "missing" else {"external": provider},
        ),
        patch.object(provider, "is_enabled", return_value=False),
        pytest.raises(HTTPException) as exc_info,
    ):
        await cast(Any, create_session).__wrapped__(
            request=AgentSessionCreate(
                entity_type=AgentSessionEntity.AGENT_PRESET,
                entity_id=uuid.uuid4(),
                backend_id="external",
            ),
            role=_read_role(uuid.uuid4()),
            session=db,
        )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Agent backend is unavailable"
    db.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("entity_type", "backend_id", "expected"),
    [
        # Chats without a choice take the configured backend.
        (AgentSessionEntity.WORKSPACE_CHAT, None, "external"),
        (AgentSessionEntity.AGENT_PRESET, None, "external"),
        (AgentSessionEntity.CASE, None, "external"),
        # An explicit choice wins over the configured backend.
        (AgentSessionEntity.AGENT_PRESET, "oss", "oss"),
        # Surfaces that need the built-in workflow keep it.
        (AgentSessionEntity.AGENT_PRESET_BUILDER, None, "oss"),
        (AgentSessionEntity.EXTERNAL_CHANNEL, None, "oss"),
        (AgentSessionEntity.WORKFLOW, None, "oss"),
    ],
)
async def test_create_session_applies_configured_chat_backend(
    entity_type: AgentSessionEntity, backend_id: str | None, expected: str
) -> None:
    session_stub = _agent_session_stub(backend_id=expected)
    fake_svc = SimpleNamespace(create_session=AsyncMock(return_value=session_stub))
    request = AgentSessionCreate(entity_type=entity_type, entity_id=uuid.uuid4())
    if backend_id is not None:
        request = AgentSessionCreate(
            entity_type=entity_type, entity_id=uuid.uuid4(), backend_id=backend_id
        )
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(),
        ),
        patch(
            "tracecat.agent.session.router.config.TRACECAT__AGENT_CHAT_BACKEND",
            "external",
        ),
    ):
        await cast(Any, create_session).__wrapped__(
            request=request,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )
    sent = fake_svc.create_session.await_args.args[0]
    assert sent.backend_id == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        AgentSessionUpdate(backend_id="external"),
        AgentSessionUpdate(harness_type="another_harness"),
    ],
)
async def test_update_session_rejects_backend_changes_with_bad_request(
    params: AgentSessionUpdate,
) -> None:
    db = AsyncMock()
    agent_session = _agent_session_stub()
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.is_legacy_session",
            return_value=False,
        ),
        patch(
            "tracecat.agent.session.router.AgentSessionService.get_session",
            return_value=agent_session,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await cast(Any, update_session).__wrapped__(
            session_id=agent_session.id,
            params=params,
            role=_read_role(agent_session.workspace_id),
            session=db,
        )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Start a new chat to change its backend"
    assert agent_session.backend_id == "oss"
    assert agent_session.harness_type == HarnessType.CLAUDE_CODE
    db.commit.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("is_child", [False, True])
@pytest.mark.parametrize("surface", ["create", "get", "vercel", "update", "fork"])
@pytest.mark.parametrize(
    "backend_state", ["enabled", "disabled", "missing", "unsupported"]
)
async def test_session_responses_derive_readonly_from_backend_state(
    surface: str, backend_state: str, is_child: bool
) -> None:
    session_stub = _agent_session_stub(
        backend_id="external",
        harness_type="unsupported" if backend_state == "unsupported" else "claude_code",
    )
    session_stub.spawned_by_session_id = uuid.uuid4() if is_child else None
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=session_stub.created_by,
        workspace_id=session_stub.workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read", "agent:execute"}),
    )
    provider = DefaultBackend()
    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        get_session=AsyncMock(return_value=session_stub),
        create_session=AsyncMock(return_value=session_stub),
        update_session=AsyncMock(return_value=session_stub),
        fork_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(return_value=[]),
    )
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
        patch.object(
            registry,
            "get_agent_backends",
            return_value={} if backend_state == "missing" else {"external": provider},
        ),
        patch.object(provider, "is_enabled", return_value=backend_state != "disabled"),
    ):
        match surface:
            case "create":
                response = await cast(Any, create_session).__wrapped__(
                    request=AgentSessionCreate(
                        entity_type=session_stub.entity_type,
                        entity_id=session_stub.entity_id,
                        backend_id="external",
                    ),
                    role=role,
                    session=AsyncMock(),
                )
            case "update":
                response = await cast(Any, update_session).__wrapped__(
                    session_id=session_stub.id,
                    params=AgentSessionUpdate(title="Renamed chat"),
                    role=role,
                    session=AsyncMock(),
                )
                fake_svc.update_session.assert_awaited_once()
            case _:
                endpoint = {
                    "get": get_session,
                    "vercel": get_session_vercel,
                    "fork": fork_session,
                }[surface]
                response = await cast(Any, endpoint).__wrapped__(
                    session_id=session_stub.id, role=role, session=AsyncMock()
                )
    assert response.backend_id == "external"
    assert response.is_readonly is (is_child or backend_state != "enabled")


def _control_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"type": "steer", "id": str(uuid.UUID(int=1)), "text": " Stop "},
            AgentSteerControl(type="steer", id=uuid.UUID(int=1), text="Stop"),
        ),
        (
            {"type": "withdraw", "id": str(uuid.UUID(int=2))},
            AgentWithdrawControl(type="withdraw", id=uuid.UUID(int=2)),
        ),
        (
            {"type": "cancel", "id": str(uuid.UUID(int=3))},
            AgentCancelControl(type="cancel", id=uuid.UUID(int=3)),
        ),
    ],
    ids=["steer", "withdraw", "cancel"],
)
def test_control_session_dispatches_each_type_to_the_service(
    body: dict[str, str],
    expected: AgentSteerControl | AgentWithdrawControl | AgentCancelControl,
) -> None:
    agent_session = _agent_session_stub(curr_run_id=uuid.uuid4())
    receipt = AgentControlReceipt(
        id=expected.id,
        type=expected.type,
        run_id=agent_session.curr_run_id,
        status="accepted",
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=agent_session),
        control=AsyncMock(return_value=receipt),
    )
    app = FastAPI()
    app.include_router(router)
    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = lambda: _control_role(
        agent_session.workspace_id
    )
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(return_value=None),
        ),
        patch("tracecat.agent.session.router.logger") as logger,
        TestClient(app) as client,
    ):
        response = client.post(f"/agent/sessions/{agent_session.id}/control", json=body)

    assert response.status_code == 200
    assert AgentControlReceipt.model_validate(response.json()) == receipt
    fake_svc.control.assert_awaited_once_with(agent_session.id, expected)
    # Every control request is logged with its type.
    assert logger.info.call_args.kwargs["control_type"] == expected.type


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,code,detail",
    [
        (
            TracecatConflictError(
                "Agent session does not have an active turn",
                detail={"lifecycle": "completed"},
            ),
            409,
            "Agent session does not have an active turn",
        ),
        (
            TracecatConflictError("This agent backend does not support withdrawing"),
            409,
            "This agent backend does not support withdrawing",
        ),
        (
            AgentControlRejected("Agent control operation failed"),
            409,
            "The turn is no longer running",
        ),
        (
            AgentControlUncertain("Agent control outcome is uncertain"),
            503,
            "Control outcome is unknown; retry with the same ID",
        ),
    ],
)
async def test_control_session_maps_control_outcomes(
    error: Exception, code: int, detail: str
) -> None:
    agent_session = _agent_session_stub(curr_run_id=uuid.uuid4())
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=agent_session),
        control=AsyncMock(side_effect=error),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(return_value=None),
        ),
    ):
        raw_control_session = cast(Any, control_session).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_control_session(
                session_id=agent_session.id,
                request=AgentSteerControl(type="steer", id=uuid.uuid4(), text="Stop"),
                role=_control_role(agent_session.workspace_id),
                session=AsyncMock(),
            )

    assert (exc_info.value.status_code, exc_info.value.detail) == (code, detail)


@pytest.mark.anyio
async def test_deprecated_cancel_route_calls_request_cancel() -> None:
    agent_session = _agent_session_stub(curr_run_id=uuid.uuid4())
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=agent_session),
        request_cancel=AsyncMock(return_value=None),
    )
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(return_value=None),
        ),
    ):
        await cast(Any, cancel_session).__wrapped__(
            session_id=agent_session.id,
            role=_control_role(agent_session.workspace_id),
            session=AsyncMock(),
            request=None,
        )
    # The `cancel` control calls the same method (see test_agent_backends).
    fake_svc.request_cancel.assert_awaited_once_with(
        agent_session.id, reason="user_cancel"
    )
    cancel_route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/{session_id}/cancel")
    )
    assert cancel_route.deprecated is True


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "steering,withdrawing", [(False, False), (True, False), (True, True)]
)
def test_session_read_reports_backend_controls(
    enabled: bool, steering: bool, withdrawing: bool
) -> None:
    session_stub = _agent_session_stub(backend_id="external")
    provider = DefaultBackend()
    with (
        patch.object(
            registry, "get_agent_backends", return_value={"external": provider}
        ),
        patch.object(provider, "is_enabled", return_value=enabled),
        patch.object(provider, "steering", steering),
        patch.object(provider, "withdrawing", withdrawing),
    ):
        read = build_session_read(
            cast(Any, session_stub), _control_role(session_stub.workspace_id)
        )
    # A disabled backend controls nothing.
    assert read.capabilities.steering is (enabled and steering)
    assert read.capabilities.withdrawing is (enabled and withdrawing)
