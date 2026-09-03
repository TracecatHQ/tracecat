"""Reusable Slack ack/error helpers for channel handlers."""

from __future__ import annotations

from slack_sdk.errors import SlackApiError, SlackClientError
from slack_sdk.web.async_client import AsyncWebClient

from tracecat.logger import logger

ACK_REACTION = "eyes"
IN_PROGRESS_REACTION = "hourglass_flowing_sand"
COMPLETE_REACTION = "white_check_mark"
ERROR_REACTION = "warning"


class SlackAlreadyAcknowledgedError(ValueError):
    """Raised when another processor already acknowledged a Slack message."""


async def _add_reaction(
    client: AsyncWebClient,
    *,
    channel_id: str,
    ts: str,
    name: str,
    ignore_already_reacted: bool = True,
) -> None:
    try:
        await client.api_call(
            api_method="reactions.add",
            params={"channel": channel_id, "timestamp": ts, "name": name},
        )
    except SlackApiError as exc:
        if ignore_already_reacted and exc.response.get("error") == "already_reacted":
            return
        raise


async def _remove_reaction(
    client: AsyncWebClient, *, channel_id: str, ts: str, name: str
) -> None:
    try:
        await client.api_call(
            api_method="reactions.remove",
            params={"channel": channel_id, "timestamp": ts, "name": name},
        )
    except SlackApiError:
        # Best-effort cleanup: ignore if reaction is already gone.
        return


async def ack_event(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Add the eyes reaction to indicate event processing has started."""

    try:
        await _add_reaction(
            client,
            channel_id=channel_id,
            ts=ts,
            name=ACK_REACTION,
            ignore_already_reacted=False,
        )
    except SlackApiError as exc:
        if exc.response.get("error") == "already_reacted":
            raise SlackAlreadyAcknowledgedError(
                "Another processor already acknowledged this message."
            ) from exc
        raise


async def remove_ack(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Remove the eyes reaction."""
    await _remove_reaction(client, channel_id=channel_id, ts=ts, name=ACK_REACTION)


async def set_in_progress(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Swap the initial ack reaction for an in-progress reaction."""

    await _remove_reaction(client, channel_id=channel_id, ts=ts, name=ACK_REACTION)
    await _add_reaction(
        client,
        channel_id=channel_id,
        ts=ts,
        name=IN_PROGRESS_REACTION,
    )


async def set_complete(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Mark processing complete on the source Slack message."""

    await _remove_reaction(
        client,
        channel_id=channel_id,
        ts=ts,
        name=IN_PROGRESS_REACTION,
    )
    await _remove_reaction(client, channel_id=channel_id, ts=ts, name=ACK_REACTION)
    await _add_reaction(
        client,
        channel_id=channel_id,
        ts=ts,
        name=COMPLETE_REACTION,
    )


async def set_error(client: AsyncWebClient, *, channel_id: str, ts: str) -> None:
    """Mark processing failed on the source Slack message."""

    await _remove_reaction(
        client,
        channel_id=channel_id,
        ts=ts,
        name=IN_PROGRESS_REACTION,
    )
    await _remove_reaction(client, channel_id=channel_id, ts=ts, name=ACK_REACTION)
    await _add_reaction(client, channel_id=channel_id, ts=ts, name=ERROR_REACTION)


async def notify_error(
    client: AsyncWebClient,
    *,
    channel_id: str,
    thread_ts: str | None,
    ts: str | None,
) -> None:
    """Notify the user in-thread that processing failed."""

    if ts:
        try:
            await set_error(client, channel_id=channel_id, ts=ts)
        except SlackClientError as exc:
            logger.warning(
                "Failed to mark Slack message with error reaction",
                channel_id=channel_id,
                ts=ts,
                error=str(exc),
            )

    await client.api_call(
        api_method="chat.postMessage",
        params={
            "channel": channel_id,
            "text": "I'm having trouble responding to your message. Please try again.",
            "thread_ts": thread_ts,
        },
    )
