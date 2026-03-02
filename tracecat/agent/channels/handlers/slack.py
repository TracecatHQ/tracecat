"""Slack external channel handler."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channels.handlers.slack_helpers import (
    SlackAlreadyAcknowledgedError,
    ack_event,
    notify_error,
    remove_ack,
)
from tracecat.agent.channels.schemas import (
    ChannelType,
    SlackChannelContext,
    ValidatedChannelToken,
)
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.schemas import BasicChatRequest
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client

SLACK_EVENT_DEDUP_TTL_SECONDS = 600
SLACK_MENTION_PATTERN = re.compile(r"<@[^>]+>")


@dataclass(frozen=True)
class SlackAppMentionContext:
    team_id: str
    channel_id: str
    thread_ts: str
    user_id: str
    event_ts: str
    bot_user_id: str
    ts: str
    event_id: str

    def to_channel_context(self) -> SlackChannelContext:
        return {
            "channel_type": ChannelType.SLACK.value,
            "team_id": self.team_id,
            "channel_id": self.channel_id,
            "thread_ts": self.thread_ts,
            "user_id": self.user_id,
            "event_ts": self.event_ts,
            "bot_user_id": self.bot_user_id,
        }


class SlackChannelHandler:
    """Process Slack event callbacks for external channel sessions."""

    def __init__(self, session: AsyncSession, role: Role):
        self.session = session
        self.role = role

    @staticmethod
    def _parse_app_mention_context(payload: dict[str, Any]) -> SlackAppMentionContext:
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValueError("Expected Slack event payload")

        if event.get("type") != "app_mention":
            raise ValueError("Unsupported Slack event type")

        ts = event.get("ts")
        channel_id = event.get("channel")
        if not isinstance(ts, str) or not isinstance(channel_id, str):
            raise ValueError("Slack event is missing message timestamp or channel")

        thread_ts = event.get("thread_ts")
        if not isinstance(thread_ts, str):
            thread_ts = ts

        event_ts = event.get("event_ts")
        if not isinstance(event_ts, str):
            event_ts = ts

        team_id = payload.get("team_id")
        event_id = payload.get("event_id")
        if not isinstance(team_id, str) or not isinstance(event_id, str):
            raise ValueError("Slack event is missing team_id or event_id")

        user_id = event.get("user")
        if not isinstance(user_id, str):
            user_id = ""

        bot_user_id = ""
        authorizations = payload.get("authorizations")
        if isinstance(authorizations, list) and authorizations:
            first_auth = authorizations[0]
            if isinstance(first_auth, dict):
                candidate = first_auth.get("user_id")
                if isinstance(candidate, str):
                    bot_user_id = candidate
        if not bot_user_id:
            authed_users = payload.get("authed_users")
            if isinstance(authed_users, list) and authed_users:
                candidate = authed_users[0]
                if isinstance(candidate, str):
                    bot_user_id = candidate

        return SlackAppMentionContext(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            event_ts=event_ts,
            bot_user_id=bot_user_id,
            ts=ts,
            event_id=event_id,
        )

    async def _is_duplicate_event(
        self, client: RedisClient, *, team_id: str, event_id: str
    ) -> bool:
        dedup_key = f"slack-event:{team_id}:{event_id}"
        inserted = await client.set_if_not_exists(
            dedup_key,
            "1",
            expire_seconds=SLACK_EVENT_DEDUP_TTL_SECONDS,
        )
        return not inserted

    async def _resolve_session_id_from_thread_metadata(
        self, client: AsyncWebClient, *, channel_id: str, thread_ts: str
    ) -> uuid.UUID | None:
        try:
            response = await client.api_call(
                api_method="conversations.replies",
                params={
                    "channel": channel_id,
                    "ts": thread_ts,
                    "limit": 100,
                    "inclusive": True,
                    "include_all_metadata": True,
                },
            )
        except SlackApiError:
            return None

        messages = response.get("messages", [])
        if not isinstance(messages, list):
            return None

        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            metadata = message.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("event_type") != "agent_session":
                continue
            event_payload = metadata.get("event_payload")
            if not isinstance(event_payload, dict):
                continue
            session_id_raw = event_payload.get("session_id")
            if not isinstance(session_id_raw, str):
                continue
            try:
                return uuid.UUID(session_id_raw)
            except ValueError:
                continue
        return None

    async def _resolve_or_create_session(
        self,
        *,
        token: ValidatedChannelToken,
        context: SlackAppMentionContext,
        slack_client: AsyncWebClient,
    ) -> uuid.UUID:
        session_service = AgentSessionService(self.session, role=self.role)

        session_id = await self._resolve_session_id_from_thread_metadata(
            slack_client,
            channel_id=context.channel_id,
            thread_ts=context.thread_ts,
        )
        if session_id is not None:
            existing = await session_service.get_session(session_id)
            if existing is not None:
                return existing.id

        created = await session_service.create_session(
            AgentSessionCreate(
                title="Slack thread",
                entity_type=AgentSessionEntity.EXTERNAL_CHANNEL,
                entity_id=token.agent_preset_id,
                channel_context=dict(context.to_channel_context()),
                agent_preset_id=token.agent_preset_id,
            )
        )
        return created.id

    @staticmethod
    def _extract_prompt_text(event: dict[str, Any]) -> str:
        raw_text = event.get("text")
        if not isinstance(raw_text, str):
            return "Please respond in this thread."

        without_mentions = SLACK_MENTION_PATTERN.sub("", raw_text).strip()
        if without_mentions:
            return without_mentions
        return raw_text.strip() or "Please respond in this thread."

    async def handle(
        self, *, payload: dict[str, Any], token: ValidatedChannelToken
    ) -> None:
        if payload.get("type") != "event_callback":
            return

        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") != "app_mention":
            return

        context = self._parse_app_mention_context(payload)
        dedup_client = await get_redis_client()
        if await self._is_duplicate_event(
            dedup_client,
            team_id=context.team_id,
            event_id=context.event_id,
        ):
            logger.info(
                "Slack event already processed",
                workspace_id=str(token.workspace_id),
                event_id=context.event_id,
                team_id=context.team_id,
            )
            return

        slack_client = AsyncWebClient(token=token.config.slack_bot_token)
        try:
            await ack_event(
                slack_client,
                channel_id=context.channel_id,
                ts=context.ts,
            )
            session_id = await self._resolve_or_create_session(
                token=token,
                context=context,
                slack_client=slack_client,
            )
            session_service = AgentSessionService(self.session, role=self.role)
            await session_service.run_turn(
                session_id,
                BasicChatRequest(message=self._extract_prompt_text(event)),
            )
        except SlackAlreadyAcknowledgedError:
            logger.info(
                "Slack message already acknowledged",
                workspace_id=str(token.workspace_id),
                event_id=context.event_id,
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
            )
            return
        except Exception:
            logger.exception(
                "Failed to process Slack app_mention",
                workspace_id=str(token.workspace_id),
                event_id=context.event_id,
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
            )
            try:
                await notify_error(
                    slack_client,
                    channel_id=context.channel_id,
                    thread_ts=context.thread_ts,
                    ts=context.ts,
                )
            except Exception:
                logger.exception("Failed to notify Slack error state")
            return
        else:
            await remove_ack(
                slack_client,
                channel_id=context.channel_id,
                ts=context.ts,
            )
            logger.info(
                "Slack app_mention resolved to session",
                workspace_id=str(token.workspace_id),
                session_id=str(session_id),
                event_id=context.event_id,
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
            )
