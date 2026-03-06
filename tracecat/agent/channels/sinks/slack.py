"""Slack stream sink for external agent channels."""

from __future__ import annotations

import json
import uuid
from time import monotonic
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from tracecat.agent.channels.handlers.slack_helpers import (
    set_complete,
    set_error,
    set_in_progress,
)
from tracecat.agent.channels.service import AgentChannelService
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client


class SlackStreamSink:
    """Maps runtime events directly to Slack stream APIs."""

    FLUSH_WINDOW_SECONDS = 0.25
    APPROVAL_BATCH_TTL_SECONDS = 24 * 60 * 60
    APPROVAL_REDIS_PREFIX = "slack-approval"

    def __init__(
        self,
        *,
        slack_bot_token: str,
        channel_id: str,
        thread_ts: str,
        recipient_user_id: str | None = None,
        recipient_team_id: str | None = None,
        reaction_ts: str | None = None,
        session_id: str,
        workspace_id: str,
    ) -> None:
        self._client = AsyncWebClient(token=slack_bot_token)
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._recipient_user_id = recipient_user_id
        self._recipient_team_id = recipient_team_id
        self._reaction_ts = reaction_ts
        self._session_id = session_id
        self._workspace_id = workspace_id
        self._stream_ts: str | None = None
        self._delta_buffer: list[str] = []
        self._last_flush_at = monotonic()
        self._final_text = ""
        self._is_closed = False
        self._in_progress_reaction_set = False
        self._tool_task_by_call_id: dict[str, tuple[str, str]] = {}
        self._pending_approval_tool_ids: set[str] = set()
        self._task_counter = 0

    @classmethod
    def _batch_key(cls, batch_id: str) -> str:
        return f"{cls.APPROVAL_REDIS_PREFIX}:batch:{batch_id}"

    @classmethod
    def _decision_key(cls, batch_id: str, tool_call_id: str) -> str:
        return f"{cls._batch_key(batch_id)}:decision:{tool_call_id}"

    @classmethod
    def _submission_key(cls, batch_id: str) -> str:
        return f"{cls._batch_key(batch_id)}:submitted"

    @property
    def _metadata(self) -> dict[str, object]:
        return {
            "event_type": "agent_session",
            "event_payload": {"session_id": self._session_id},
        }

    @staticmethod
    def _extract_stream_ts(response: dict[str, Any]) -> str | None:
        match response:
            case {"ts": str(ts)}:
                return ts
            case {"message": {"ts": str(ts)}}:
                return ts
            case _:
                return None

    async def _ensure_stream_started(self) -> None:
        if self._stream_ts is not None:
            return
        payload: dict[str, Any] = {
            "channel": self._channel_id,
            "thread_ts": self._thread_ts,
            "task_display_mode": "timeline",
        }
        if self._recipient_user_id and self._recipient_team_id:
            payload["recipient_user_id"] = self._recipient_user_id
            payload["recipient_team_id"] = self._recipient_team_id
        response = await self._client.api_call(
            api_method="chat.startStream",
            json=payload,
        )
        response_data = response.data
        if not isinstance(response_data, dict):
            raise ValueError("Slack chat.startStream returned an invalid response")
        stream_ts = self._extract_stream_ts(response_data)
        if stream_ts is None:
            raise ValueError("Slack chat.startStream did not return a stream ts")
        self._stream_ts = stream_ts

    async def _append_chunks(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        await self._ensure_stream_started()
        await self._client.api_call(
            api_method="chat.appendStream",
            json={
                "channel": self._channel_id,
                "ts": self._stream_ts,
                "chunks": chunks,
            },
        )

    async def _append_stream_text(self, text: str) -> None:
        if not text:
            return
        await self._append_chunks([{"type": "markdown_text", "text": text}])
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

    async def _set_in_progress_reaction(self) -> None:
        if self._in_progress_reaction_set:
            return
        if self._reaction_ts is None:
            return
        await set_in_progress(
            self._client,
            channel_id=self._channel_id,
            ts=self._reaction_ts,
        )
        self._in_progress_reaction_set = True

    async def _set_terminal_reaction(self, *, is_error: bool) -> None:
        if self._reaction_ts is None:
            return
        if is_error:
            await set_error(
                self._client,
                channel_id=self._channel_id,
                ts=self._reaction_ts,
            )
            return
        await set_complete(
            self._client,
            channel_id=self._channel_id,
            ts=self._reaction_ts,
        )

    def _next_task_id(self) -> str:
        self._task_counter += 1
        return f"task_{self._task_counter}"

    @staticmethod
    def _tool_title(tool_name: str | None) -> str:
        if not tool_name:
            return "Tool call"
        if "." in tool_name:
            return tool_name.split(".")[-1].replace("_", " ").strip().title()
        return tool_name.replace("_", " ").strip().title()

    @staticmethod
    def _coerce_output_text(value: Any, *, max_len: int = 1200) -> str:
        if isinstance(value, str):
            output = value
        else:
            try:
                output = json.dumps(value, default=str)
            except TypeError:
                output = str(value)
        if len(output) <= max_len:
            return output
        return f"{output[:max_len]}..."

    @classmethod
    def _format_tool_args_preview(cls, value: Any, *, max_len: int = 600) -> str:
        if value is None:
            return "{}"
        if isinstance(value, dict):
            output = json.dumps(value, indent=2, sort_keys=True, default=str)
        else:
            output = cls._coerce_output_text(value, max_len=max_len)
        output = cls._coerce_output_text(output, max_len=max_len)
        return output

    @staticmethod
    def _parse_json_if_possible(value: str) -> Any:
        stripped = value.strip()
        if not stripped or stripped[0] not in "{[":
            return value
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _looks_like_approval_interrupt_output(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        lowered = value.lower()
        if "doesn't want to take this action" in lowered:
            return True
        if "stop what you are doing and wait for the user" in lowered:
            return True
        if "request interrupted by user" in lowered:
            return True
        return False

    @classmethod
    def _normalize_tool_output(cls, value: Any) -> Any:
        if isinstance(value, list) and value:
            text_parts: list[str] = []
            for item in value:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ):
                    text_parts.append(item["text"])
                else:
                    return value
            joined = "\n".join(part.strip() for part in text_parts if part.strip())
            return cls._parse_json_if_possible(joined)

        if isinstance(value, str):
            return cls._parse_json_if_possible(value)

        return value

    @classmethod
    def _format_tool_error_output(cls, value: Any) -> str:
        match cls._normalize_tool_output(value):
            case str() as text if message := text.strip():
                return message
            case {"message": str(message)} if message := message.strip():
                return message
            case {"error": str(message)} if message := message.strip():
                return message
            case _:
                return "Tool execution failed"

    def _resolve_task(
        self,
        *,
        tool_call_id: str | None,
        tool_name: str | None,
    ) -> tuple[str, str]:
        if tool_call_id and tool_call_id in self._tool_task_by_call_id:
            return self._tool_task_by_call_id[tool_call_id]

        task_id = tool_call_id or self._next_task_id()
        title = self._tool_title(tool_name)
        if tool_call_id:
            self._tool_task_by_call_id[tool_call_id] = (task_id, title)
        return task_id, title

    async def _emit_task_update(
        self,
        *,
        tool_call_id: str | None,
        tool_name: str | None,
        status: str,
        details: str | None = None,
    ) -> None:
        task_id, title = self._resolve_task(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )

        chunk: dict[str, Any] = {
            "type": "task_update",
            "id": task_id,
            "title": title,
            "status": status,
        }
        if details:
            chunk["details"] = details
        logger.info(
            "Sending Slack task_update chunk",
            session_id=self._session_id,
            workspace_id=self._workspace_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
        )
        await self._append_chunks([chunk])

    async def _persist_approval_batch(
        self,
        *,
        batch_id: str,
        items: list[Any],
    ) -> None:
        redis = await get_redis_client()
        batch_payload = {
            "batch_id": batch_id,
            "session_id": self._session_id,
            "workspace_id": self._workspace_id,
            "channel_id": self._channel_id,
            "thread_ts": self._thread_ts,
            "tool_call_ids": [str(item.id) for item in items],
            "tool_names": {
                str(item.id): str(item.name)
                for item in items
                if isinstance(item.id, str) and isinstance(item.name, str)
            },
        }
        await redis.set(
            self._batch_key(batch_id),
            json.dumps(batch_payload, separators=(",", ":")),
            expire_seconds=self.APPROVAL_BATCH_TTL_SECONDS,
        )

    async def _post_approval_card(
        self,
        *,
        batch_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any] | None,
    ) -> None:
        approve_value = AgentChannelService.create_slack_approval_action_token(
            batch_id=batch_id,
            tool_call_id=tool_call_id,
            action="approve",
        )
        deny_value = AgentChannelService.create_slack_approval_action_token(
            batch_id=batch_id,
            tool_call_id=tool_call_id,
            action="deny",
        )
        args_preview = self._format_tool_args_preview(tool_input)

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Approval required*\n"
                        f"*Tool:* `{tool_name}`\n"
                        f"*Args:*\n```{args_preview}```"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "tracecat_approval_approve",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "value": approve_value,
                    },
                    {
                        "type": "button",
                        "action_id": "tracecat_approval_deny",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "value": deny_value,
                    },
                ],
            },
        ]

        await self._client.api_call(
            api_method="chat.postMessage",
            json={
                "channel": self._channel_id,
                "thread_ts": self._thread_ts,
                "text": f"Approval required for {tool_name}",
                "blocks": blocks,
            },
        )

    async def _emit_approval_cards(self, items: list[Any]) -> None:
        if not items:
            return

        batch_id = uuid.uuid4().hex
        await self._persist_approval_batch(batch_id=batch_id, items=items)

        for item in items:
            try:
                await self._post_approval_card(
                    batch_id=batch_id,
                    tool_call_id=item.id,
                    tool_name=item.name,
                    tool_input=item.input,
                )
            except SlackApiError as exc:
                logger.warning(
                    "Failed to post Slack approval card",
                    session_id=self._session_id,
                    workspace_id=self._workspace_id,
                    tool_call_id=item.id,
                    tool_name=item.name,
                    error=str(exc),
                )

    async def _stop_stream(self, *, final_error_text: str | None = None) -> None:
        if self._is_closed:
            return

        is_error = final_error_text is not None
        try:
            await self._flush_delta_buffer(force=True)
            if final_error_text:
                await self._append_stream_text(final_error_text)

            if self._stream_ts is None:
                return

            try:
                await self._client.api_call(
                    api_method="chat.stopStream",
                    json={
                        "channel": self._channel_id,
                        "ts": self._stream_ts,
                        "metadata": json.dumps(self._metadata),
                    },
                )
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
                    json={"channel": self._channel_id, "ts": self._stream_ts},
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
                    json={
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
            await self._set_terminal_reaction(is_error=is_error)
            self._is_closed = True

    async def append(self, event: UnifiedStreamEvent) -> None:
        if self._is_closed:
            return

        if event.type not in (StreamEventType.DONE, StreamEventType.ERROR):
            await self._set_in_progress_reaction()

        if event.type is StreamEventType.TEXT_DELTA and event.text:
            self._delta_buffer.append(event.text)
            await self._flush_delta_buffer(force=False)
            return

        if event.type in (
            StreamEventType.TOOL_CALL_START,
            StreamEventType.TOOL_CALL_STOP,
        ):
            # Avoid noisy timeline rows for repeated tool-attempt lifecycle events.
            return

        if event.type is StreamEventType.TOOL_RESULT:
            if not event.tool_call_id:
                logger.debug(
                    "Skipping Slack tool result without tool_call_id",
                    session_id=self._session_id,
                    workspace_id=self._workspace_id,
                    event_type=event.type,
                    tool_name=event.tool_name,
                )
                return
            if (
                event.is_error
                and event.tool_call_id is not None
                and event.tool_call_id in self._pending_approval_tool_ids
            ):
                logger.info(
                    "Ignoring transient tool error for pending approval",
                    session_id=self._session_id,
                    workspace_id=self._workspace_id,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                )
                return

            if event.is_error and self._looks_like_approval_interrupt_output(
                event.tool_output
            ):
                logger.debug(
                    "Skipping synthetic approval interruption tool result",
                    session_id=self._session_id,
                    workspace_id=self._workspace_id,
                    tool_call_id=event.tool_call_id,
                )
                return
            self._pending_approval_tool_ids.discard(event.tool_call_id)

            status = "error" if event.is_error else "complete"
            details: str | None = None
            if event.is_error:
                details = self._format_tool_error_output(event.tool_output)

            await self._flush_delta_buffer(force=True)
            await self._emit_task_update(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                status=status,
                details=details,
            )
            return

        if event.type is StreamEventType.APPROVAL_REQUEST and event.approval_items:
            await self._flush_delta_buffer(force=True)
            chunks: list[dict[str, Any]] = []
            for item in event.approval_items:
                self._pending_approval_tool_ids.add(item.id)
                task_id, title = self._resolve_task(
                    tool_call_id=item.id,
                    tool_name=item.name,
                )
                chunks.append(
                    {
                        "type": "task_update",
                        "id": task_id,
                        "title": title,
                        "status": "pending",
                        "details": "Waiting for approval",
                    }
                )
            await self._append_chunks(chunks)
            await self._emit_approval_cards(list(event.approval_items))
            return

        if event.type is StreamEventType.TEXT_STOP:
            await self._flush_delta_buffer(force=True)
            return

        if event.type is StreamEventType.DONE:
            await self._stop_stream()
            return

        # Runtime ERROR events are finalized via error().
        if event.type is StreamEventType.ERROR:
            return

    async def error(self, error: str) -> None:
        await self._stop_stream(final_error_text=f"\n\nError: {error}")

    async def done(self) -> None:
        await self._stop_stream()
