import uuid
from collections.abc import Sequence

import orjson
from sqlmodel import col, select

from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.agent.models import ModelInfo, RunAgentArgs, ToolFilters
from tracecat.agent.runtime import ModelMessageTA
from tracecat.agent.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)
from tracecat.cases.prompts import CaseCopilotPrompts
from tracecat.cases.service import CasesService
from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatMessage, ChatRequest, ChatResponse
from tracecat.chat.tools import get_default_tools
from tracecat.db.schemas import Case, Chat, Runbook
from tracecat.identifiers import UserID
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.runbook.prompts import RunbookCopilotPrompts
from tracecat.service import BaseWorkspaceService
from tracecat.types.exceptions import TracecatNotFoundError


class ChatService(BaseWorkspaceService):
    service_name = "chat"

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

    async def _get_case(self, case_id: uuid.UUID) -> Case:
        """Get a case by ID."""
        cases_service = CasesService(self.session, self.role)
        case = await cases_service.get_case(case_id)
        if not case:
            raise TracecatNotFoundError(f"Case with ID {case_id} not found")
        return case

    async def _get_runbook(self, runbook_id: uuid.UUID) -> Runbook | None:
        """Get a runbook by ID. Can be None if not found."""
        stmt = select(Runbook).where(
            Runbook.id == runbook_id,
            Runbook.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        runbook = result.first()
        return runbook

    async def _chat_entity_to_prompt(self, entity_type: str, chat: Chat) -> str:
        """Get the prompt for a given entity type."""

        if entity_type == ChatEntity.CASE:
            case = await self._get_case(chat.entity_id)
            return CaseCopilotPrompts(case=case).instructions
        elif entity_type == ChatEntity.RUNBOOK:
            runbook = await self._get_runbook(chat.entity_id)
            return RunbookCopilotPrompts(runbook=runbook).instructions
        else:
            raise ValueError(
                f"Unsupported chat entity type: {entity_type}. Expected one of: {list(ChatEntity)}"
            )

    async def start_chat_turn(
        self,
        chat_id: uuid.UUID,
        request: ChatRequest,
        executor: BaseAgentExecutor,
    ) -> ChatResponse:
        """Start a new chat turn with an AI agent.

        This method handles the business logic for starting a chat turn,
        including instruction merging, content injection, and agent execution.
        """
        # Get the chat
        chat = await self.get_chat(chat_id)
        if not chat:
            raise TracecatNotFoundError(f"Chat with ID {chat_id} not found")

        # Prepare agent execution arguments
        instructions = await self._chat_entity_to_prompt(chat.entity_type, chat)
        model_info = ModelInfo(
            name=request.model_name,
            provider=request.model_provider,
            base_url=request.base_url,
        )
        args = RunAgentArgs(
            role=self.role,
            user_prompt=request.message,
            tool_filters=ToolFilters(actions=chat.tools),
            session_id=str(chat_id),
            instructions=instructions,
            model_info=model_info,
        )

        # Start agent execution
        await executor.start(args)

        # Return response with stream URL
        stream_url = f"/api/chat/{chat_id}/stream"
        return ChatResponse(
            stream_url=stream_url,
            chat_id=chat_id,
        )

    async def get_chat(self, chat_id: uuid.UUID) -> Chat | None:
        """Get a chat by ID, ensuring it belongs to the current workspace."""
        stmt = select(Chat).where(
            Chat.id == chat_id,
            Chat.owner_id == self.workspace_id,
        )

        result = await self.session.exec(stmt)
        return result.first()

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
