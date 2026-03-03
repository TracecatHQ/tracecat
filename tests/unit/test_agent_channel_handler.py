from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.agent.channels.handlers.slack import (
    SLACK_EVENT_DEDUP_TTL_SECONDS,
    SlackChannelHandler,
)
from tracecat import config
from tracecat.agent.channels.schemas import (
    ChannelType,
    SlackChannelTokenConfig,
    ValidatedChannelToken,
)
from tracecat.agent.channels.service import AgentChannelService
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


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
    session_row = SimpleNamespace(channel_context=None)
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
    assert session_row.channel_context == context.to_channel_context()
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
