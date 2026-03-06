from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from tracecat import config
from tracecat.agent.channels.handlers.slack import (
    SLACK_EVENT_DEDUP_TTL_SECONDS,
    SlackChannelHandler,
)
from tracecat.agent.channels.schemas import (
    ChannelType,
    SlackChannelTokenConfig,
    ValidatedChannelToken,
)
from tracecat.agent.channels.service import AgentChannelService
from tracecat.auth.types import Role
from tracecat.chat.schemas import BasicChatRequest
from tracecat.exceptions import TracecatValidationError


class _FakeSlackResponse:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {"ok": True}


class _FakeSlackErrorResponse(dict[str, object]):
    status_code = 403


class _FakeSlackClient:
    def __init__(
        self,
        *,
        token: str,
        responses: dict[str, dict[str, object]] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.token = token
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.responses = responses or {}
        self.errors = errors or {}

    async def api_call(
        self,
        *,
        api_method: str,
        params: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
    ) -> _FakeSlackResponse:
        payload = params if params is not None else (json or {})
        self.calls.append((api_method, payload))
        if error := self.errors.get(api_method):
            raise error
        return _FakeSlackResponse(self.responses.get(api_method))


@pytest.mark.anyio
async def test_post_ephemeral_notice_targets_thread_when_available() -> None:
    fake_client = cast(Any, _FakeSlackClient(token="xoxb-test"))

    await SlackChannelHandler._post_ephemeral_notice(
        fake_client,
        channel_id="C1",
        user_id="U1",
        text="Session is locked",
        thread_ts="1700000000.100",
    )

    assert fake_client.calls == [
        (
            "chat.postEphemeral",
            {
                "channel": "C1",
                "user": "U1",
                "text": "Session is locked",
                "thread_ts": "1700000000.100",
            },
        )
    ]


def test_parse_app_mention_context_uses_thread_ts_or_falls_back_to_ts() -> None:
    payload = {
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "channel": "C1",
            "user": "U1",
        },
        "authorizations": [{"user_id": "B1"}],
    }

    context = SlackChannelHandler._parse_app_mention_context(payload)

    assert context.channel_id == "C1"
    assert context.thread_ts == "1700000000.123"
    assert context.bot_user_id == "B1"
    assert context.to_channel_context().get("channel_type") == "slack"
    assert context.to_channel_context().get("message_ts") == "1700000000.123"


def test_extract_prompt_text_strips_mentions() -> None:
    text = SlackChannelHandler._extract_prompt_text({"text": "<@U123> summarize this"})
    assert text == "summarize this"


def test_extract_prompt_text_falls_back_when_only_mention() -> None:
    text = SlackChannelHandler._extract_prompt_text({"text": "<@U123>"})
    assert text == "<@U123>"


@pytest.mark.anyio
async def test_resolve_mentioning_user_profile_returns_identity_fields() -> None:
    handler = SlackChannelHandler(session=AsyncMock(), role=AsyncMock())
    fake_client = cast(
        Any,
        _FakeSlackClient(
            token="xoxb-test",
            responses={
                "users.info": {
                    "ok": True,
                    "user": {
                        "name": "jordan",
                        "real_name": "Jordan Lim",
                        "profile": {
                            "display_name": "Jordan",
                            "real_name": "Jordan Lim",
                            "email": "jordan@example.com",
                        },
                    },
                }
            },
        ),
    )

    profile = await handler._resolve_mentioning_user_profile(
        fake_client,
        user_id="U1",
        workspace_id=uuid.uuid4(),
    )

    assert profile is not None
    assert profile.user_id == "U1"
    assert profile.username == "jordan"
    assert profile.display_name == "Jordan"
    assert profile.real_name == "Jordan Lim"
    assert profile.email == "jordan@example.com"


def test_parse_approval_action_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "test-signing-secret")
    token = AgentChannelService.create_slack_approval_action_token(
        batch_id="batch-1",
        tool_call_id="tool-1",
        action="approve",
    )
    payload = {
        "type": "block_actions",
        "actions": [{"value": token}],
        "user": {"id": "U1", "username": "jordan"},
        "channel": {"id": "C1"},
        "container": {"message_ts": "1700000000.111", "thread_ts": "1700000000.100"},
    }

    context = SlackChannelHandler._parse_approval_action_context(payload)

    assert context.batch_id == "batch-1"
    assert context.tool_call_id == "tool-1"
    assert context.action == "approve"
    assert context.channel_id == "C1"
    assert context.thread_ts == "1700000000.100"
    assert context.message_ts == "1700000000.111"
    assert context.user_id == "U1"
    assert context.user_name == "jordan"


def test_parse_approval_action_context_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "test-signing-secret")
    payload = {
        "type": "block_actions",
        "actions": [{"value": "bad.token"}],
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "container": {"message_ts": "1700000000.111"},
    }

    with pytest.raises(TracecatValidationError):
        SlackChannelHandler._parse_approval_action_context(payload)


@pytest.mark.anyio
async def test_resolve_or_create_session_reuses_existing_thread_context_session() -> (
    None
):
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    existing_session_id = uuid.uuid4()
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    context = SlackChannelHandler._parse_app_mention_context(
        {
            "team_id": "T1",
            "event_id": "Ev1",
            "event": {
                "type": "app_mention",
                "ts": "1700000000.123",
                "thread_ts": "1700000000.100",
                "channel": "C1",
                "user": "U1",
            },
        }
    )

    with (
        patch.object(
            handler,
            "_resolve_session_id_from_thread_metadata",
            AsyncMock(return_value=None),
        ),
        patch.object(
            handler,
            "_resolve_session_id_from_channel_context",
            AsyncMock(return_value=existing_session_id),
        ),
        patch.object(
            handler,
            "_persist_session_channel_context",
            AsyncMock(return_value=True),
        ) as persist_mock,
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.create_session",
            new_callable=AsyncMock,
        ) as create_session_mock,
    ):
        session_id = await handler._resolve_or_create_session(
            token=token,
            context=context,
            slack_client=AsyncMock(),
        )

    assert session_id == existing_session_id
    persist_mock.assert_awaited_once()
    create_session_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_persist_session_channel_context_scopes_metadata_to_token_preset() -> (
    None
):
    workspace_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    session = AsyncMock()
    session_row = SimpleNamespace(
        channel_context={
            "channel_type": "slack",
            "active_sink": "slack",
            "custom_key": "preserve-me",
        }
    )
    execute_result = SimpleNamespace(
        scalar_one_or_none=lambda: session_row,
    )
    session.execute = AsyncMock(return_value=execute_result)

    handler = SlackChannelHandler(session=session, role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=preset_id,
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    context = SlackChannelHandler._parse_app_mention_context(
        {
            "team_id": "T1",
            "event_id": "Ev1",
            "event": {
                "type": "app_mention",
                "ts": "1700000000.123",
                "thread_ts": "1700000000.100",
                "channel": "C1",
                "user": "U1",
            },
        }
    )

    persisted = await handler._persist_session_channel_context(
        token=token,
        context=context,
        session_id=uuid.uuid4(),
    )

    assert persisted is True
    assert session_row.channel_context["active_sink"] == "slack"
    assert session_row.channel_context["custom_key"] == "preserve-me"
    assert session_row.channel_context["thread_ts"] == "1700000000.100"
    session.commit.assert_awaited_once()
    await_args = session.execute.await_args
    assert await_args is not None
    stmt_sql = str(await_args.args[0])
    assert "agent_session.entity_type" in stmt_sql
    assert "agent_session.agent_preset_id" in stmt_sql


@pytest.mark.anyio
async def test_handle_continues_when_redis_dedup_unavailable() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "thread_ts": "1700000000.100",
            "channel": "C1",
            "user": "U1",
            "text": "<@Ubot> summarize this",
        },
    }

    with (
        patch(
            "tracecat.agent.channels.handlers.slack.get_redis_client",
            AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.ack_event",
            new_callable=AsyncMock,
        ) as ack_event_mock,
        patch.object(
            handler,
            "_resolve_or_create_session",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.run_turn",
            new_callable=AsyncMock,
        ) as run_turn_mock,
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.get_session",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.has_pending_approvals",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.set_in_progress",
            new_callable=AsyncMock,
        ) as set_in_progress_mock,
    ):
        await handler.handle(payload=payload, token=token)

    ack_event_mock.assert_awaited_once()
    run_turn_mock.assert_awaited_once()
    set_in_progress_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_clears_dedup_key_after_processing_failure() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "thread_ts": "1700000000.100",
            "channel": "C1",
            "user": "U1",
            "text": "<@Ubot> summarize this",
        },
    }
    dedup_client = AsyncMock()
    dedup_client.set_if_not_exists.return_value = True
    dedup_client.delete = AsyncMock(return_value=1)

    with (
        patch(
            "tracecat.agent.channels.handlers.slack.get_redis_client",
            AsyncMock(return_value=dedup_client),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.ack_event",
            new_callable=AsyncMock,
        ),
        patch.object(
            handler,
            "_resolve_or_create_session",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.run_turn",
            new_callable=AsyncMock,
            side_effect=RuntimeError("run_turn failed"),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.get_session",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.has_pending_approvals",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.notify_error",
            new_callable=AsyncMock,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.set_in_progress",
            new_callable=AsyncMock,
        ) as set_in_progress_mock,
    ):
        await handler.handle(payload=payload, token=token)

    dedup_client.set_if_not_exists.assert_awaited_once_with(
        "slack-event:T1:Ev1",
        "1",
        expire_seconds=SLACK_EVENT_DEDUP_TTL_SECONDS,
    )
    dedup_client.delete.assert_awaited_once_with("slack-event:T1:Ev1")
    set_in_progress_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_app_mention_allows_session_when_inbox_was_previous_source() -> (
    None
):
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "thread_ts": "1700000000.100",
            "channel": "C1",
            "user": "U1",
            "text": "<@Ubot> summarize this",
        },
    }

    fake_client = _FakeSlackClient(token="xoxb-test")

    with (
        patch(
            "tracecat.agent.channels.handlers.slack.AsyncWebClient",
            return_value=fake_client,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.get_redis_client",
            AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.ack_event",
            new_callable=AsyncMock,
        ),
        patch.object(
            handler,
            "_resolve_or_create_session",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.get_session",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(channel_context={"active_sink": "inbox"}),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.has_pending_approvals",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.run_turn",
            new_callable=AsyncMock,
        ) as run_turn_mock,
        patch(
            "tracecat.agent.channels.handlers.slack.set_in_progress",
            new_callable=AsyncMock,
        ) as set_in_progress_mock,
    ):
        await handler.handle(payload=payload, token=token)

    run_turn_mock.assert_awaited_once()
    set_in_progress_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_rejects_app_mention_when_pending_approvals_exist() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "thread_ts": "1700000000.100",
            "channel": "C1",
            "user": "U1",
            "text": "<@Ubot> summarize this",
        },
    }

    fake_client = _FakeSlackClient(token="xoxb-test")

    with (
        patch(
            "tracecat.agent.channels.handlers.slack.AsyncWebClient",
            return_value=fake_client,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.get_redis_client",
            AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.ack_event",
            new_callable=AsyncMock,
        ),
        patch.object(
            handler,
            "_resolve_or_create_session",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.get_session",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(channel_context={}),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.has_pending_approvals",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.run_turn",
            new_callable=AsyncMock,
        ) as run_turn_mock,
        patch(
            "tracecat.agent.channels.handlers.slack.set_in_progress",
            new_callable=AsyncMock,
        ) as set_in_progress_mock,
    ):
        await handler.handle(payload=payload, token=token)

    assert fake_client.calls
    assert fake_client.calls[-1][0] == "chat.postMessage"
    run_turn_mock.assert_not_awaited()
    set_in_progress_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_handle_passes_resolved_slack_actor_instructions_to_run_turn() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "thread_ts": "1700000000.100",
            "channel": "C1",
            "user": "U1",
            "text": "<@Ubot> summarize this",
        },
    }
    fake_client = _FakeSlackClient(
        token="xoxb-test",
        responses={
            "users.info": {
                "ok": True,
                "user": {
                    "name": "jordan",
                    "profile": {
                        "display_name": "Jordan",
                        "real_name": "Jordan Lim",
                        "email": "jordan@example.com",
                    },
                },
            }
        },
    )

    with (
        patch(
            "tracecat.agent.channels.handlers.slack.AsyncWebClient",
            return_value=fake_client,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.get_redis_client",
            AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.ack_event",
            new_callable=AsyncMock,
        ),
        patch.object(
            handler,
            "_resolve_or_create_session",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.get_session",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(channel_context={"active_sink": "slack"}),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.has_pending_approvals",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.run_turn",
            new_callable=AsyncMock,
        ) as run_turn_mock,
        patch(
            "tracecat.agent.channels.handlers.slack.set_in_progress",
            new_callable=AsyncMock,
        ),
    ):
        await handler.handle(payload=payload, token=token)

    await_args = run_turn_mock.await_args
    assert await_args is not None
    request = await_args.args[1]
    assert isinstance(request, BasicChatRequest)
    assert request.message == "summarize this"
    assert request.instructions is not None
    assert "Slack user ID: U1" in request.instructions
    assert "Slack display name: Jordan" in request.instructions
    assert "Slack real name: Jordan Lim" in request.instructions
    assert "Slack email: jordan@example.com" in request.instructions
    assert "<@U1>" in request.instructions


@pytest.mark.anyio
async def test_handle_falls_back_to_user_id_when_profile_lookup_missing_scope() -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:update"}),
    )
    handler = SlackChannelHandler(session=AsyncMock(), role=role)
    token = ValidatedChannelToken(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
        channel_type=ChannelType.SLACK,
        config=SlackChannelTokenConfig(
            slack_bot_token="xoxb-test",
            slack_signing_secret="signing-secret",
        ),
        public_token="public-token",
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "ts": "1700000000.123",
            "thread_ts": "1700000000.100",
            "channel": "C1",
            "user": "U1",
            "text": "<@Ubot> summarize this",
        },
    }
    fake_client = _FakeSlackClient(
        token="xoxb-test",
        errors={
            "users.info": SlackApiError(
                message="missing_scope",
                response=_FakeSlackErrorResponse(
                    {"ok": False, "error": "missing_scope"}
                ),
            )
        },
    )

    with (
        patch(
            "tracecat.agent.channels.handlers.slack.AsyncWebClient",
            return_value=fake_client,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.get_redis_client",
            AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.ack_event",
            new_callable=AsyncMock,
        ),
        patch.object(
            handler,
            "_resolve_or_create_session",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.get_session",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(channel_context={"active_sink": "slack"}),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.has_pending_approvals",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "tracecat.agent.channels.handlers.slack.AgentSessionService.run_turn",
            new_callable=AsyncMock,
        ) as run_turn_mock,
        patch(
            "tracecat.agent.channels.handlers.slack.set_in_progress",
            new_callable=AsyncMock,
        ),
    ):
        await handler.handle(payload=payload, token=token)

    await_args = run_turn_mock.await_args
    assert await_args is not None
    request = await_args.args[1]
    assert isinstance(request, BasicChatRequest)
    assert request.instructions is not None
    assert "Slack user ID: U1" in request.instructions
    assert "Slack email:" not in request.instructions
