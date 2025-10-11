import uuid

from pydantic_ai.messages import ModelMessage

from tracecat.chat.service import ChatService
from tracecat.logger import logger


class DBMessageStore:
    async def load(self, session_id: uuid.UUID) -> list[ModelMessage]:
        async with ChatService.with_session() as svc:
            try:
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

    async def store(self, session_id: uuid.UUID, messages: list[ModelMessage]) -> None:
        async with ChatService.with_session() as svc:
            await svc.append_messages(session_id, messages)
