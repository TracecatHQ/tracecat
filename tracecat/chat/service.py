import uuid
from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

import tracecat.agent.adapter.vercel
from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.agent.models import AgentConfig, ModelMessageTA, RunAgentArgs
from tracecat.agent.service import AgentManagementService
from tracecat.cases.prompts import CaseCopilotPrompts
from tracecat.cases.service import CasesService
from tracecat.chat.enums import ChatEntity, MessageKind
from tracecat.chat.models import (
    BasicChatRequest,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    VercelChatRequest,
)
from tracecat.chat.tools import get_default_tools
from tracecat.db.schemas import Case, Chat, Runbook
from tracecat.db.schemas import ChatMessage as DBChatMessage
from tracecat.identifiers import UserID
from tracecat.logger import logger
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

        Supports both simple text messages and Vercel UI messages.
        """

        # Get the chat
        chat = await self.get_chat(chat_id)
        if not chat:
            raise TracecatNotFoundError(f"Chat with ID {chat_id} not found")

        # Handle different request formats
        match request:
            case VercelChatRequest(
                message=ui_message,
            ):
                # Convert Vercel UI messages to pydantic-ai messages
                [message] = tracecat.agent.adapter.vercel.convert_ui_message(ui_message)
                match message:
                    case ModelRequest(parts=[UserPromptPart(content=content)]):
                        match content:
                            case str(s):
                                user_prompt = s
                            case list(l):
                                user_prompt = "\n".join(str(item) for item in l)
                            case _:
                                raise ValueError(f"Unsupported user prompt: {content}")
                    case _:
                        raise ValueError(f"Unsupported message: {message}")
            case BasicChatRequest(
                message=user_prompt,
            ):
                pass
            case _:
                raise ValueError(f"Unsupported chat request: {request}")

        logger.info(
            "Received user prompt",
            prompt_length=len(user_prompt),
        )
        # Prepare agent execution arguments
        instructions = await self._chat_entity_to_prompt(chat.entity_type, chat)

        # Start agent execution
        agent_svc = AgentManagementService(self.session, self.role)
        # TODO: Allow model to change per turn
        async with agent_svc.with_model_config() as model_config:
            args = RunAgentArgs(
                user_prompt=user_prompt,
                session_id=chat_id,
                config=AgentConfig(
                    instructions=instructions,
                    model_name=model_config.name,
                    model_provider=model_config.provider,
                    actions=chat.tools,
                ),
            )
            await executor.start(args)

        # Return response with stream URL
        stream_url = f"/api/chat/{chat_id}/stream"
        return ChatResponse(stream_url=stream_url, chat_id=chat_id)

    async def get_chat(
        self, chat_id: uuid.UUID, *, with_messages: bool = False
    ) -> Chat | None:
        """Get a chat by ID, ensuring it belongs to the current workspace."""
        stmt = select(Chat).where(
            Chat.id == chat_id,
            Chat.owner_id == self.workspace_id,
        )
        if with_messages:
            stmt = stmt.options(selectinload(Chat.messages))  # type: ignore
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

    async def append_message(
        self,
        chat_id: uuid.UUID,
        message: ModelMessage,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> DBChatMessage:
        """Persist a message to the database."""
        db_message = DBChatMessage(
            chat_id=chat_id,
            kind=kind.value,
            owner_id=self.workspace_id,
            data=ModelMessageTA.dump_python(message, mode="json"),
        )

        self.session.add(db_message)
        await self.session.commit()
        await self.session.refresh(db_message)

        logger.debug(
            "Persisted message to database",
            chat_id=chat_id,
            message_id=db_message.id,
            kind=kind.value,
        )

        return db_message

    async def append_messages(
        self,
        chat_id: uuid.UUID,
        messages: Sequence[ModelMessage],
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None:
        """Persist multiple messages to the database in a single transaction."""
        if not messages:
            return

        # Create all DB message objects at once
        db_messages = [
            DBChatMessage(
                chat_id=chat_id,
                kind=kind.value,
                owner_id=self.workspace_id,
                data=ModelMessageTA.dump_python(message, mode="json"),
            )
            for message in messages
        ]

        # Add all messages to session at once
        self.session.add_all(db_messages)

        await self.session.commit()

        logger.debug(
            "Persisted multiple messages to database",
            chat_id=chat_id,
            message_count=len(db_messages),
            kind=kind.value,
        )

    async def list_messages(
        self,
        chat_id: uuid.UUID,
    ) -> list[ModelMessage]:
        """Retrieve all messages for a chat from the database."""
        stmt = (
            select(DBChatMessage)
            .where(
                DBChatMessage.chat_id == chat_id,
                DBChatMessage.owner_id == self.workspace_id,
            )
            .order_by(col(DBChatMessage.created_at).asc())
        )

        result = await self.session.exec(stmt)
        db_messages = result.all()

        messages: list[ModelMessage] = []
        for db_msg in db_messages:
            validated_msg = ModelMessageTA.validate_python(db_msg.data)
            messages.append(validated_msg)
        return messages

    async def get_chat_messages(self, chat: Chat) -> list[ChatMessage]:
        """Get chat messages from database, with Redis backfill if needed."""
        # First, try to get messages from the database
        db_messages = await self.list_messages(chat.id)
        # Convert to ChatMessage format for API compatibility
        parsed_messages: list[ChatMessage] = []
        for idx, msg in enumerate(db_messages):
            # Use index as ID for now (or could use timestamp)
            msg_with_id = ChatMessage(id=str(idx), message=msg)
            parsed_messages.append(msg_with_id)

        return parsed_messages
