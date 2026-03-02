"""Reusable Slack ack/error helpers for channel handlers."""

from __future__ import annotations

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


class SlackAlreadyAcknowledgedError(ValueError):
    """Raised when another processor already acknowledged a Slack message."""


async def ack_event(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Add the eyes reaction to indicate event processing has started."""

    try:
        await client.api_call(
            api_method="reactions.add",
            params={"channel": channel_id, "timestamp": ts, "name": "eyes"},
        )
    except SlackApiError as exc:
        if exc.response.get("error") == "already_reacted":
            raise SlackAlreadyAcknowledgedError(
                "Another processor already acknowledged this message."
            ) from exc
        raise


async def remove_ack(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Remove the eyes reaction."""

    try:
        await client.api_call(
            api_method="reactions.remove",
            params={"channel": channel_id, "timestamp": ts, "name": "eyes"},
        )
    except SlackApiError:
        # Best-effort cleanup: ignore if reaction is already gone.
        return


async def notify_error(
    client: AsyncWebClient,
    *,
    channel_id: str,
    thread_ts: str | None,
    ts: str | None,
) -> None:
    """Notify the user in-thread that processing failed."""

    if ts:
        await remove_ack(client, channel_id=channel_id, ts=ts)
        try:
            await client.api_call(
                api_method="reactions.add",
                params={"channel": channel_id, "timestamp": ts, "name": "warning"},
            )
        except SlackApiError:
            pass

    await client.api_call(
        api_method="chat.postMessage",
        params={
            "channel": channel_id,
            "text": "I'm having trouble responding to your message. Please try again.",
            "thread_ts": thread_ts,
        },
    )
