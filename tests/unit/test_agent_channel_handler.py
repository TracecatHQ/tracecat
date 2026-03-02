from __future__ import annotations

from tracecat.agent.channels.handlers.slack import SlackChannelHandler


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


def test_extract_prompt_text_strips_mentions() -> None:
    text = SlackChannelHandler._extract_prompt_text({"text": "<@U123> summarize this"})
    assert text == "summarize this"


def test_extract_prompt_text_falls_back_when_only_mention() -> None:
    text = SlackChannelHandler._extract_prompt_text({"text": "<@U123>"})
    assert text == "<@U123>"
