import uuid
from collections.abc import Sequence

import orjson
import yaml
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.agent.agent.runtime import ModelMessageTA
from tracecat.agent.agent.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)
from tracecat.cases.service import CasesService
from tracecat.chat.models import ChatMessage
from tracecat.chat.tools import get_default_tools
from tracecat.db.schemas import Chat
from tracecat.identifiers import UserID
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.service import BaseWorkspaceService
from tracecat.settings.service import get_setting_cached
from tracecat.types.auth import Role


class ChatService(BaseWorkspaceService):
    service_name = "chat"

    async def list_chats(
        self,
        *,
        user_id: UserID,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Chat]:
        """List chats for the current workspace with optional entity filtering."""

        stmt = select(Chat).where(Chat.owner_id == self.role.workspace_id)
        if user_id:
            stmt = stmt.where(Chat.user_id == user_id)

        if entity_type:
            stmt = stmt.where(Chat.entity_type == entity_type)

        if entity_id:
            stmt = stmt.where(Chat.entity_id == entity_id)

        stmt = stmt.order_by(col(Chat.created_at).desc()).limit(limit)

        result = await self.session.exec(stmt)
        return result.all()

    async def create_chat(
        self,
        *,
        title: str,
        entity_type: str,
        entity_id: uuid.UUID,
        tools: list[str] | None = None,
    ) -> Chat:
        """Create a new chat associated with an entity."""
        if self.role.user_id is None:
            raise ValueError("User ID is required")

        chat = Chat(
            title=title,
            user_id=self.role.user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            owner_id=self.workspace_id,
            tools=tools or get_default_tools(entity_type),
        )

        self.session.add(chat)
        await self.session.commit()
        await self.session.refresh(chat)

        logger.info(
            "Created chat",
            chat_id=str(chat.id),
            title=title,
            entity_type=entity_type,
            entity_id=entity_id,
            workspace_id=self.workspace_id,
        )

        return chat

    async def get_chat(self, chat_id: uuid.UUID) -> Chat | None:
        """Get a chat by ID, ensuring it belongs to the current workspace."""
        stmt = select(Chat).where(
            Chat.id == chat_id,
            Chat.owner_id == self.workspace_id,
        )

        result = await self.session.exec(stmt)
        return result.first()

    async def update_chat(
        self,
        chat: Chat,
        *,
        tools: list[str] | None = None,
        title: str | None = None,
    ) -> Chat:
        """Update chat properties."""
        # Update fields if provided
        if tools is not None:
            chat.tools = tools
        if title is not None:
            chat.title = title

        self.session.add(chat)
        await self.session.commit()
        await self.session.refresh(chat)

        return chat

    async def get_chat_messages(self, chat: Chat) -> list[ChatMessage]:
        """Get chat messages from Redis stream."""
        try:
            redis_client = await get_redis_client()
            stream_key = f"agent-stream:{chat.id}"

            # Read all messages from the Redis stream
            messages = await redis_client.xrange(stream_key, min_id="-", max_id="+")

            # Handle case where stream doesn't exist or has expired
            if not messages:
                logger.info(
                    "No messages found in Redis stream (may have expired)",
                    stream_key=stream_key,
                    chat_id=chat.id,
                )
                return []

            parsed_messages: list[ChatMessage] = []
            for id, fields in messages:
                try:
                    data = orjson.loads(fields[DATA_KEY])

                    # Skip end-of-stream markers
                    if data.get(END_TOKEN) == END_TOKEN_VALUE:
                        continue

                    validated_msg = ModelMessageTA.validate_python(data)
                    msg_with_id = ChatMessage(id=id, message=validated_msg)
                    parsed_messages.append(msg_with_id)

                except (orjson.JSONDecodeError, KeyError) as e:
                    logger.warning(
                        "Failed to parse Redis message",
                        message_id=id,
                        error=str(e),
                    )
                    continue

            return parsed_messages

        except Exception as e:
            logger.error(
                "Failed to fetch chat messages from Redis",
                chat_id=chat.id,
                error=str(e),
            )
            return []


async def inject_case_content(
    *, session: AsyncSession, role: Role, case_id: uuid.UUID
) -> str | None:
    if await get_setting_cached(
        "agent_case_chat_inject_content",
        session=session,
        default=False,
    ):
        case_svc = CasesService(session, role)
        if case := await case_svc.get_case(case_id):
            # Add indication that this is the current case
            # Prepare case data for YAML dump, including tags if they exist
            case_data = case.model_dump(mode="json")
            if case.tags:
                case_data["tags"] = [tag.name for tag in case.tags]

            case_content = (
                f"This is the current case you are working on:\n\n"
                "<case_context>\n"
                f"```yaml\n"
                f"{yaml.dump(case_data, indent=2)}\n"
                "```\n"
                "</case_context>\n"
            )

            return case_content
    return None
