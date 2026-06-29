from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette import status

from tracecat.agent.adapter.vercel import UIMessage
from tracecat.agent.common.stream_types import (
    HarnessType,
    StreamEventType,
    UnifiedStreamEvent,
)
from tracecat.agent.session.router import (
    fork_session,
    get_session,
    get_session_vercel,
    list_sessions,
    remove_session_artifact,
    send_message,
    stream_session_events,
)
from tracecat.agent.session.schemas import AgentSessionForkRequest
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.artifacts.schemas import CaseArtifact
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus
from tracecat.chat.schemas import (
    ApprovalDecision,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.exceptions import EntitlementRequired, TracecatNotFoundError


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
        "agents_binding": {"enabled": False},
        "harness_type": HarnessType.CLAUDE_CODE,
        "created_at": now,
        "updated_at": now,
        "last_stream_id": None,
        "artifacts": [],
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


@pytest.mark.anyio
async def test_list_sessions_service_account_filters_null_created_by() -> None:
    workspace_id = uuid.uuid4()
    role = _service_account_role(workspace_id)
    fake_svc = SimpleNamespace(list_sessions=AsyncMock(return_value=[]))

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_list_sessions = cast(Any, list_sessions).__wrapped__
        response = await raw_list_sessions(
            role=role,
            session=AsyncMock(),
            entity_type=None,
            entity_id=None,
            exclude_entity_types=None,
            parent_session_id=None,
            limit=100,
        )

    assert response == []
    fake_svc.list_sessions.assert_awaited_once_with(
        created_by=None,
        filter_created_by_none=True,
        entity_type=None,
        entity_id=None,
        exclude_entity_types=[AgentSessionEntity.WORKSPACE_CHAT],
        parent_session_id=None,
        limit=100,
    )


@pytest.mark.anyio
async def test_list_sessions_user_filters_by_user_id() -> None:
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    role = Role(
        type="user",
        service_id="tracecat-api",
        user_id=user_id,
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )
    fake_svc = SimpleNamespace(list_sessions=AsyncMock(return_value=[]))

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_list_sessions = cast(Any, list_sessions).__wrapped__
        response = await raw_list_sessions(
            role=role,
            session=AsyncMock(),
            entity_type=None,
            entity_id=None,
            exclude_entity_types=None,
            parent_session_id=None,
            limit=100,
        )

    assert response == []
    fake_svc.list_sessions.assert_awaited_once_with(
        created_by=user_id,
        filter_created_by_none=False,
        entity_type=None,
        entity_id=None,
        exclude_entity_types=[AgentSessionEntity.WORKSPACE_CHAT],
        parent_session_id=None,
        limit=100,
    )


@pytest.mark.anyio
async def test_get_session_includes_agents_binding() -> None:
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
        raw_get_session = cast(Any, get_session).__wrapped__
        response = await raw_get_session(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    assert response.model_dump(mode="json")["agents_binding"] == {
        "enabled": False,
        "subagents": [],
    }


@pytest.mark.anyio
async def test_get_session_vercel_includes_agents_binding() -> None:
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
        raw_get_session_vercel = cast(Any, get_session_vercel).__wrapped__
        response = await raw_get_session_vercel(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    assert response.model_dump(mode="json")["agents_binding"] == {
        "enabled": False,
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
async def test_get_workspace_chat_session_requires_entitlement() -> None:
    session_stub = _agent_session_stub(entity_type=AgentSessionEntity.WORKSPACE_CHAT)
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(return_value=[]),
        list_artifacts=Mock(return_value=[]),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService",
            return_value=fake_svc,
        ),
        patch(
            "tracecat.agent.session.router.require_workspace_chat_entitlement_for_entity",
            AsyncMock(side_effect=EntitlementRequired("workspace_chat")),
        ),
    ):
        raw_get_session = cast(Any, get_session).__wrapped__
        with pytest.raises(EntitlementRequired):
            await raw_get_session(
                session_id=session_stub.id,
                role=_read_role(session_stub.workspace_id),
                session=AsyncMock(),
            )


@pytest.mark.anyio
async def test_get_session_requires_entitlement_for_workspace_chat_parent() -> None:
    parent_session = _agent_session_stub(entity_type=AgentSessionEntity.WORKSPACE_CHAT)
    child_session = _agent_session_stub(
        entity_type=AgentSessionEntity.APPROVAL,
        parent_session_id=parent_session.id,
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(side_effect=[child_session, parent_session]),
        list_messages=AsyncMock(return_value=[]),
        list_artifacts=Mock(return_value=[]),
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
        raw_get_session = cast(Any, get_session).__wrapped__
        with pytest.raises(EntitlementRequired):
            await raw_get_session(
                session_id=child_session.id,
                role=_read_role(child_session.workspace_id),
                session=AsyncMock(),
            )

    fake_svc.list_messages.assert_not_awaited()


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


@pytest.mark.anyio
async def test_send_message_continue_uses_path_session_id_for_stream_key() -> None:
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
        validate_turn_request=AsyncMock(return_value=agent_session),
        run_turn=AsyncMock(return_value=None),
        build_initial_artifact=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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
        response = await raw_send_message(
            session_id=session_id,
            request=request,
            role=role,
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )

    assert isinstance(response, StreamingResponse)
    with_session_mock.assert_called_once_with(role=role)
    stream_new_mock.assert_awaited_once_with(session_id, workspace_id)
    fake_svc.validate_turn_request.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )
    fake_stream.reset_for_new_turn.assert_not_awaited()
    fake_stream.sse.assert_called_once()
    assert fake_stream.sse.call_args.kwargs["last_id"] == "$"
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )


@pytest.mark.anyio
async def test_send_message_new_turn_resets_stream_before_streaming() -> None:
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
        run_turn=AsyncMock(return_value=None),
        should_seed_initial_artifact=AsyncMock(return_value=False),
        build_initial_artifact=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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
        ),
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

    assert isinstance(response, StreamingResponse)
    with_session_mock.assert_called_once_with(role=role)
    fake_stream.reset_for_new_turn.assert_awaited_once()
    fake_svc.should_seed_initial_artifact.assert_awaited_once_with(agent_session)
    fake_svc.build_initial_artifact.assert_not_awaited()
    fake_stream.abort_new_turn.assert_not_awaited()
    fake_stream.sse.assert_called_once()
    assert fake_stream.sse.call_args.kwargs["last_id"] == "0-0"


@pytest.mark.anyio
async def test_send_message_new_turn_appends_initial_artifact() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    agent_session = _agent_session_stub(id=session_id, workspace_id=workspace_id)
    artifact = CaseArtifact(
        id=str(agent_session.entity_id),
        title="Investigate suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )
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
        run_turn=AsyncMock(return_value=None),
        should_seed_initial_artifact=AsyncMock(return_value=True),
        build_initial_artifact=AsyncMock(return_value=artifact),
        apply_artifact_side_effects=AsyncMock(return_value=[artifact]),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        append=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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
        response = await raw_send_message(
            session_id=session_id,
            request=request,
            role=role,
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )

    assert isinstance(response, StreamingResponse)
    fake_stream.reset_for_new_turn.assert_awaited_once()
    fake_svc.should_seed_initial_artifact.assert_awaited_once_with(agent_session)
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
    fake_svc.apply_artifact_side_effects.assert_awaited_once()
    apply_args = fake_svc.apply_artifact_side_effects.await_args.args
    assert apply_args[0] == session_id
    assert len(apply_args[1]) == 1
    assert apply_args[1][0].op == "upsert"
    assert apply_args[1][0].artifact == artifact
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )


@pytest.mark.anyio
async def test_send_message_new_turn_skips_initial_artifact_after_first_prompt() -> (
    None
):
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    agent_session = _agent_session_stub(id=session_id, workspace_id=workspace_id)
    artifact = CaseArtifact(
        id=str(agent_session.entity_id),
        title="Investigate suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-2",
            role="user",
            parts=[{"type": "text", "text": "next turn"}],
        ),
        model="gpt-4o-mini",
        model_provider="openai",
    )

    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=agent_session),
        run_turn=AsyncMock(return_value=None),
        should_seed_initial_artifact=AsyncMock(return_value=False),
        build_initial_artifact=AsyncMock(return_value=artifact),
        apply_artifact_side_effects=AsyncMock(return_value=[artifact]),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        append=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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
        response = await raw_send_message(
            session_id=session_id,
            request=request,
            role=role,
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )

    assert isinstance(response, StreamingResponse)
    fake_stream.reset_for_new_turn.assert_awaited_once()
    fake_svc.should_seed_initial_artifact.assert_awaited_once_with(agent_session)
    fake_svc.build_initial_artifact.assert_not_awaited()
    fake_svc.apply_artifact_side_effects.assert_not_awaited()
    fake_stream.append.assert_not_awaited()
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )


@pytest.mark.anyio
async def test_send_message_new_turn_clears_stream_when_startup_fails() -> None:
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
        run_turn=AsyncMock(side_effect=RuntimeError("temporal unavailable")),
        should_seed_initial_artifact=AsyncMock(return_value=False),
        build_initial_artifact=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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
    fake_stream.reset_for_new_turn.assert_awaited_once()
    fake_svc.should_seed_initial_artifact.assert_awaited_once_with(agent_session)
    fake_svc.build_initial_artifact.assert_not_awaited()
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )
    fake_stream.abort_new_turn.assert_awaited_once()
    fake_stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_send_message_does_not_reset_stream_when_validation_fails() -> None:
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
        validate_turn_request=AsyncMock(
            side_effect=TracecatNotFoundError("No active workflow run")
        ),
        run_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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
        with pytest.raises(HTTPException, match="No active workflow run") as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == 404
    with_session_mock.assert_called_once_with(role=role)
    stream_new_mock.assert_awaited_once_with(session_id, workspace_id)
    fake_stream.reset_for_new_turn.assert_not_awaited()
    fake_stream.abort_new_turn.assert_not_awaited()
    fake_stream.sse.assert_not_called()
    fake_svc.run_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_send_message_requires_entitlement_for_workspace_chat_parent() -> None:
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
        parent_session_id=parent_session.id,
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
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
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

    fake_stream.reset_for_new_turn.assert_not_awaited()
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


def _make_stream_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


@pytest.mark.anyio
async def test_stream_session_events_returns_204_when_no_turn_started() -> None:
    """last_stream_id=None with no Last-Event-ID header returns 204."""
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    fake_session = SimpleNamespace(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        last_stream_id=None,
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        get_session=AsyncMock(return_value=fake_session),
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
    ):
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(headers={}),
            session_id=session_id,
        )

    assert isinstance(response, Response)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    fake_stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_stream_session_events_attaches_when_turn_in_progress_no_events_yet() -> (
    None
):
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    fake_session = SimpleNamespace(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        last_stream_id="0-0",
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        get_session=AsyncMock(return_value=fake_session),
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
    ):
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(
                headers={}, is_disconnected=AsyncMock(return_value=False)
            ),
            session_id=session_id,
        )

    assert isinstance(response, StreamingResponse)
    fake_stream.sse.assert_called_once()


@pytest.mark.anyio
async def test_stream_session_events_attaches_when_last_event_id_present() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    fake_session = SimpleNamespace(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        last_stream_id=None,
    )
    fake_svc = SimpleNamespace(
        session=AsyncMock(),
        get_session=AsyncMock(return_value=fake_session),
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
async def test_stream_session_events_requires_entitlement_for_legacy_workspace_chat() -> (
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
        with pytest.raises(EntitlementRequired):
            await raw(
                role=role,
                request=SimpleNamespace(
                    headers={"Last-Event-ID": "1234-0"},
                    is_disconnected=AsyncMock(return_value=False),
                ),
                session_id=session_id,
            )

    fake_stream.sse.assert_not_called()
