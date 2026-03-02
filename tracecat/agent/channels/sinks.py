"""Stream sinks for external agent channels."""

from __future__ import annotations

from time import monotonic
from typing import Any, Protocol, runtime_checkable

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.logger import logger


@runtime_checkable
class ExternalChannelSink(Protocol):
    """Sink interface used by LoopbackHandler for external channels."""

    async def append(self, event: UnifiedStreamEvent) -> None:
        """Append a runtime stream event."""

    async def error(self, error: str) -> None:
        """Emit a terminal error."""

    async def done(self) -> None:
        """Emit a terminal completion signal."""


class SlackStreamSink:
    """Maps runtime events directly to Slack stream APIs."""

    FLUSH_WINDOW_SECONDS = 0.25

    def __init__(
        self,
        *,
        slack_bot_token: str,
        channel_id: str,
        thread_ts: str,
        session_id: str,
        workspace_id: str,
    ) -> None:
        self._client = AsyncWebClient(token=slack_bot_token)
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._session_id = session_id
        self._workspace_id = workspace_id
        self._stream_ts: str | None = None
        self._delta_buffer: list[str] = []
        self._last_flush_at = monotonic()
        self._final_text = ""
        self._is_closed = False

    @property
    def _metadata(self) -> dict[str, object]:
        return {
            "event_type": "agent_session",
            "event_payload": {"session_id": self._session_id},
        }

    @staticmethod
    def _extract_stream_ts(response: dict[str, Any]) -> str | None:
        ts = response.get("ts")
        if isinstance(ts, str):
            return ts
        message = response.get("message")
        if isinstance(message, dict):
            message_ts = message.get("ts")
            if isinstance(message_ts, str):
                return message_ts
        return None

    async def _ensure_stream_started(self) -> None:
        if self._stream_ts is not None:
            return
        response = await self._client.api_call(
            api_method="chat.startStream",
            params={"channel": self._channel_id, "thread_ts": self._thread_ts},
        )
        response_data = response.data
        if not isinstance(response_data, dict):
            raise ValueError("Slack chat.startStream returned an invalid response")
        stream_ts = self._extract_stream_ts(response_data)
        if stream_ts is None:
            raise ValueError("Slack chat.startStream did not return a stream ts")
        self._stream_ts = stream_ts

    async def _append_stream_text(self, text: str) -> None:
        if not text:
            return
        await self._ensure_stream_started()
        await self._client.api_call(
            api_method="chat.appendStream",
            params={
                "channel": self._channel_id,
                "ts": self._stream_ts,
                "markdown_text": text,
            },
        )
        self._final_text += text

    async def _flush_delta_buffer(self, *, force: bool) -> None:
        if not self._delta_buffer:
            return
        now = monotonic()
        if not force and now - self._last_flush_at < self.FLUSH_WINDOW_SECONDS:
            return
        chunk = "".join(self._delta_buffer)
        self._delta_buffer.clear()
        self._last_flush_at = now
        await self._append_stream_text(chunk)

    async def _stop_stream(self, *, final_error_text: str | None = None) -> None:
        if self._is_closed:
            return

        await self._flush_delta_buffer(force=True)
        if final_error_text:
            await self._append_stream_text(final_error_text)

        if self._stream_ts is None:
            self._is_closed = True
            return

        try:
            await self._client.api_call(
                api_method="chat.stopStream",
                params={
                    "channel": self._channel_id,
                    "ts": self._stream_ts,
                    "metadata": self._metadata,
                },
            )
            self._is_closed = True
            return
        except SlackApiError as exc:
            logger.warning(
                "Slack stopStream with metadata failed; attempting fallback",
                session_id=self._session_id,
                workspace_id=self._workspace_id,
                error=str(exc),
            )

        try:
            await self._client.api_call(
                api_method="chat.stopStream",
                params={"channel": self._channel_id, "ts": self._stream_ts},
            )
        except SlackApiError as exc:
            logger.warning(
                "Slack stopStream fallback failed",
                session_id=self._session_id,
                workspace_id=self._workspace_id,
                error=str(exc),
            )

        try:
            await self._client.api_call(
                api_method="chat.update",
                params={
                    "channel": self._channel_id,
                    "ts": self._stream_ts,
                    "text": self._final_text or " ",
                    "metadata": self._metadata,
                },
            )
        except SlackApiError as exc:
            logger.warning(
                "Slack chat.update metadata fallback failed",
                session_id=self._session_id,
                workspace_id=self._workspace_id,
                error=str(exc),
            )
        finally:
            self._is_closed = True

    async def append(self, event: UnifiedStreamEvent) -> None:
        if self._is_closed:
            return

        if event.type is StreamEventType.TEXT_DELTA and event.text:
            self._delta_buffer.append(event.text)
            await self._flush_delta_buffer(force=False)
            return

        if event.type in (StreamEventType.TEXT_STOP, StreamEventType.DONE):
            await self._stop_stream()
            return

        # Runtime ERROR events are finalized via error().
        if event.type is StreamEventType.ERROR:
            return

    async def error(self, error: str) -> None:
        await self._stop_stream(final_error_text=f"\n\nError: {error}")

    async def done(self) -> None:
        await self._stop_stream()
