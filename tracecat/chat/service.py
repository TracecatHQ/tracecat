"""Read-only access to legacy chats for backward compatibility."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ChatMessage
from tracecat.db.models import Chat
from tracecat.db.models import ChatMessage as DBChatMessage
from tracecat.identifiers import UserID
from tracecat.service import BaseWorkspaceService


class ChatService(BaseWorkspaceService):
    """Read-only access to legacy ``Chat`` and ``ChatMessage`` rows."""

    service_name = "chat"

    async def get_legacy_chat(
        self, chat_id: uuid.UUID, *, with_messages: bool = False
    ) -> Chat | None:
        stmt = select(Chat).where(
            Chat.id == chat_id,
            Chat.workspace_id == self.workspace_id,
        )
        if with_messages:
            stmt = stmt.options(selectinload(Chat.messages))
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_legacy_chats(
        self,
        *,
        user_id: UserID | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Chat]:
        stmt = select(Chat).where(Chat.workspace_id == self.workspace_id)
        if user_id:
            stmt = stmt.where(Chat.user_id == user_id)
        if entity_type:
            stmt = stmt.where(Chat.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(Chat.entity_id == entity_id)
        stmt = stmt.order_by(Chat.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_legacy_messages(
        self,
        chat_id: uuid.UUID,
        *,
        kinds: Sequence[MessageKind] | None = None,
    ) -> list[ChatMessage]:
        stmt = (
            select(DBChatMessage)
            .where(
                DBChatMessage.chat_id == chat_id,
                DBChatMessage.workspace_id == self.workspace_id,
            )
            .order_by(DBChatMessage.created_at.asc())
        )
        if kinds:
            stmt = stmt.where(DBChatMessage.kind.in_({kind.value for kind in kinds}))
        result = await self.session.execute(stmt)
        return [
            message
            for db_msg in result.scalars().all()
            if (message := ChatMessage.from_db(db_msg)) is not None
        ]
