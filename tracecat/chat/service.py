"""Legacy Chat service for backward compatibility.

This service provides read-only access to legacy Chat/ChatMessage tables.
New sessions should use AgentSessionService via /agent/sessions endpoints.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.types import (
    ClaudeSDKMessageTA,
    ModelMessageTA,
    UnifiedMessage,
)
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ChatMessage
from tracecat.db.models import Chat
from tracecat.db.models import ChatMessage as DBChatMessage
from tracecat.identifiers import UserID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService


def _serialize_message(message: UnifiedMessage) -> tuple[str, dict[str, Any]]:
    """Detect message type and serialize with appropriate TypeAdapter.

    Args:
        message: A UnifiedMessage (either pydantic-ai or Claude SDK message)

    Returns:
        Tuple of (harness_type, serialized_data)
    """
    if isinstance(message, (ModelRequest, ModelResponse)):
        return HarnessType.PYDANTIC_AI.value, ModelMessageTA.dump_python(
            message, mode="json"
        )
    else:
        # Claude SDK message types
        return HarnessType.CLAUDE_CODE.value, ClaudeSDKMessageTA.dump_python(
            message, mode="json"
        )


class ChatService(BaseWorkspaceService):
    """Legacy Chat service for backward compatibility.

    Provides read-only access to legacy Chat/ChatMessage tables.
    For new functionality, use AgentSessionService.
    """

    service_name = "chat"

    async def get_legacy_chat(
        self, chat_id: uuid.UUID, *, with_messages: bool = False
    ) -> Chat | None:
        """Get a legacy chat by ID.

        Args:
            chat_id: The chat UUID.
            with_messages: Whether to eagerly load messages.

        Returns:
            The Chat if found, None otherwise.
        """
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
        """List legacy chats for the current workspace.

        Args:
            user_id: Filter by user who owns the chat.
            entity_type: Filter by entity type.
            entity_id: Filter by entity ID.
            limit: Maximum number of results.

        Returns:
            List of Chat models.
        """
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
        """List messages from legacy ChatMessage table.

        Args:
            chat_id: The chat UUID.
            kinds: Optional list of message kinds to filter by.

        Returns:
            List of ChatMessage objects.
        """
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
        db_messages = result.scalars().all()

        return [ChatMessage.from_db(db_msg) for db_msg in db_messages]

    async def append_message(
        self,
        chat_id: uuid.UUID,
        message: UnifiedMessage,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> DBChatMessage:
        """Persist a message to the legacy ChatMessage table.

        Note: This is kept for backward compatibility. New sessions
        use AgentSessionHistory instead.

        Args:
            chat_id: The chat UUID.
            message: The message to persist.
            kind: The message kind.

        Returns:
            The created DBChatMessage.
        """
        harness, data = _serialize_message(message)
        db_message = DBChatMessage(
            chat_id=chat_id,
            kind=kind.value,
            harness=harness,
            workspace_id=self.workspace_id,
            data=data,
        )

        self.session.add(db_message)
        await self.session.commit()
        await self.session.refresh(db_message)

        logger.debug(
            "Persisted message to legacy ChatMessage table",
            chat_id=chat_id,
            message_id=db_message.id,
            kind=kind.value,
            harness=harness,
        )

        return db_message

    async def append_messages(
        self,
        chat_id: uuid.UUID,
        messages: Sequence[UnifiedMessage],
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None:
        """Persist multiple messages to the legacy ChatMessage table.

        Note: This is kept for backward compatibility. New sessions
        use AgentSessionHistory instead.

        Args:
            chat_id: The chat UUID.
            messages: The messages to persist.
            kind: The message kind.
        """
        if not messages:
            return

        db_messages = []
        for message in messages:
            harness, data = _serialize_message(message)
            db_messages.append(
                DBChatMessage(
                    chat_id=chat_id,
                    kind=kind.value,
                    harness=harness,
                    workspace_id=self.workspace_id,
                    data=data,
                )
            )

        self.session.add_all(db_messages)
        await self.session.commit()

        logger.debug(
            "Persisted multiple messages to legacy ChatMessage table",
            chat_id=chat_id,
            message_count=len(db_messages),
            kind=kind.value,
        )
