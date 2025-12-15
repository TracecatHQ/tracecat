import uuid
from collections.abc import Sequence

from claude_agent_sdk import Message as ClaudeSDKMessage
from pydantic_ai.messages import ModelMessage

from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.logger import logger


class DBMessageStore:
    async def load(
        self, session_id: uuid.UUID
    ) -> list[ModelMessage | ClaudeSDKMessage]:
        """Load messages, respecting their original version."""
        async with ChatService.with_session() as svc:
            try:
                chat_messages = await svc.list_all_messages(
                    session_id, kinds=[MessageKind.CHAT_MESSAGE]
                )
                logger.info(
                    "Loaded message history",
                    message_count=len(chat_messages),
                )
                # Return raw messages - already typed correctly by ChatMessage.from_db
                return [msg.message for msg in chat_messages]
            except Exception as e:
                logger.warning(
                    "Failed to load message history from database, starting fresh",
                    error=str(e),
                    session_id=session_id,
                )
                return []

    async def store(
        self,
        session_id: uuid.UUID,
        messages: Sequence[ModelMessage | ClaudeSDKMessage],
        *,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None:
        """Store messages (version inferred from structure on load)."""
        async with ChatService.with_session() as svc:
            # Wrap messages in ChatMessage (version inferred on load)
            chat_messages = [
                ChatMessage(
                    id=str(uuid.uuid4()),
                    message=msg,
                )
                for msg in messages
            ]
            await svc.append_messages(session_id, chat_messages, kind=kind)
