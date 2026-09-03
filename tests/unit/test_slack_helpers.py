from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from slack_sdk.errors import SlackClientError

from tracecat.agent.channels.handlers.slack_helpers import notify_error


@pytest.mark.anyio
async def test_notify_error_continues_when_reaction_update_hits_slack_error() -> None:
    client = AsyncMock()

    with (
        patch(
            "tracecat.agent.channels.handlers.slack_helpers.set_error",
            new_callable=AsyncMock,
            side_effect=SlackClientError("reaction update failed"),
        ),
        patch(
            "tracecat.agent.channels.handlers.slack_helpers.logger.warning"
        ) as warning,
    ):
        await notify_error(
            client,
            channel_id="C123",
            thread_ts="1700000000.001",
            ts="1700000000.001",
        )

    warning.assert_called_once()
    client.api_call.assert_awaited_once_with(
        api_method="chat.postMessage",
        params={
            "channel": "C123",
            "text": "I'm having trouble responding to your message. Please try again.",
            "thread_ts": "1700000000.001",
        },
    )


@pytest.mark.anyio
async def test_notify_error_propagates_unexpected_reaction_update_errors() -> None:
    client = AsyncMock()

    with patch(
        "tracecat.agent.channels.handlers.slack_helpers.set_error",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected failure"),
    ):
        with pytest.raises(RuntimeError, match="unexpected failure"):
            await notify_error(
                client,
                channel_id="C123",
                thread_ts="1700000000.001",
                ts="1700000000.001",
            )

    client.api_call.assert_not_awaited()
