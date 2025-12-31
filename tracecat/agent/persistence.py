import uuid
from collections.abc import Sequence

from tracecat.agent.types import UnifiedMessage
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.logger import logger


class DBMessageStore:
    """Message store for persisting chat messages to the database."""

    async def load(self, session_id: uuid.UUID) -> list[ChatMessage]:
        """Load all messages for a session.

        Returns raw ChatMessage objects - caller is responsible for filtering
        by harness type and deserializing as needed.
        """
        async with ChatService.with_session() as svc:
            try:
                # Load ALL message kinds
                message_history = await svc.list_messages(session_id)
                logger.info(
                    "Loaded message history",
                    message_count=len(message_history),
                )
            except Exception as e:
                logger.warning(
                    "Failed to load message history from database, starting fresh",
                    error=str(e),
                    session_id=session_id,
                )
                message_history = []
        return message_history

    async def store(
        self,
        session_id: uuid.UUID,
        messages: Sequence[UnifiedMessage],
        *,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None:
        """Store messages to the database."""
        async with ChatService.with_session() as svc:
            await svc.append_messages(session_id, messages, kind=kind)
