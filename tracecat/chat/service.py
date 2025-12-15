import contextlib
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

from pydantic_ai import ModelResponse
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolResults
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import tracecat.agent.adapter.vercel
from tracecat.agent.builder.tools import build_agent_preset_builder_tools
from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.agent.preset.prompts import AgentPresetBuilderPrompt
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig, ModelMessageTA
from tracecat.audit.logger import audit_log
from tracecat.cases.prompts import CaseCopilotPrompts
from tracecat.cases.service import CasesService
from tracecat.chat.enums import ChatEntity, MessageKind
from tracecat.chat.schemas import (
    BasicChatRequest,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatUpdate,
    ClaudeMessageTA,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.chat.tools import get_default_tools
from tracecat.db.models import Case, Chat
from tracecat.db.models import ChatMessage as DBChatMessage
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import UserID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.workspaces.prompts import WorkspaceCopilotPrompts


class ChatService(BaseWorkspaceService):
    service_name = "chat"

    @audit_log(resource_type="chat", action="create")
    async def create_chat(
        self,
        *,
        title: str,
        entity_type: str,
        entity_id: uuid.UUID,
        tools: list[str] | None = None,
        agent_preset_id: uuid.UUID | None = None,
    ) -> Chat:
        """Create a new chat associated with an entity."""
        if self.role.user_id is None:
            raise ValueError("User ID is required")

        if agent_preset_id:
            preset_service = AgentPresetService(self.session, self.role)
            if not await preset_service.get_preset(agent_preset_id):
                raise TracecatNotFoundError(
                    f"Agent preset with ID '{agent_preset_id}' not found"
                )

        chat = Chat(
            title=title,
            user_id=self.role.user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            workspace_id=self.workspace_id,
            tools=tools or get_default_tools(entity_type),
            agent_preset_id=agent_preset_id,
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

    async def _chat_entity_to_prompt(self, entity_type: str, chat: Chat) -> str:
        """Get the prompt for a given entity type."""

        if entity_type == ChatEntity.CASE:
            case = await self._get_case(chat.entity_id)
            return CaseCopilotPrompts(case=case).instructions
        if entity_type == ChatEntity.AGENT_PRESET_BUILDER:
            agent_preset_service = AgentPresetService(self.session, self.role)
            if not (preset := await agent_preset_service.get_preset(chat.entity_id)):
                raise TracecatNotFoundError(
                    f"Agent preset with ID '{chat.entity_id}' not found"
                )
            prompt = AgentPresetBuilderPrompt(preset=preset)
            return prompt.instructions
        if entity_type == ChatEntity.COPILOT:
            return WorkspaceCopilotPrompts().instructions
        else:
            raise ValueError(
                f"Unsupported chat entity type: {entity_type}. Expected one of: {list(ChatEntity)}"
            )

    @contextlib.asynccontextmanager
    async def _build_agent_config(self, chat: Chat) -> AsyncIterator[AgentConfig]:
        """Build agent configuration for a chat based on its entity type.

        This helper method extracts the shared logic for building agent configs
        across different chat entity types (case, agent_preset, agent_preset_builder).

        Args:
            chat: The chat entity to build config for.

        Returns:
            AgentConfig: The configured agent config.

        Raises:
            ValueError: If the chat entity type is unsupported.
            TracecatNotFoundError: If required resources are not found.
        """
        agent_svc = AgentManagementService(self.session, self.role)
        chat_entity = ChatEntity(chat.entity_type)

        if chat_entity is ChatEntity.CASE:
            entity_instructions = await self._chat_entity_to_prompt(
                chat.entity_type, chat
            )
            if chat.agent_preset_id:
                async with agent_svc.with_preset_config(
                    preset_id=chat.agent_preset_id
                ) as preset_config:
                    combined_instructions = (
                        f"{preset_config.instructions}\n\n{entity_instructions}"
                        if preset_config.instructions
                        else entity_instructions
                    )
                    config = replace(preset_config, instructions=combined_instructions)
                    if not config.actions and chat.tools:
                        config.actions = chat.tools
                    yield config
            else:
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions=entity_instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=chat.tools,
                    )
        elif chat_entity is ChatEntity.AGENT_PRESET:
            # Live chat uses workspace-level credentials
            async with agent_svc.with_preset_config(
                preset_id=chat.entity_id, use_workspace_credentials=True
            ) as preset_config:
                config = replace(preset_config)
                if not config.actions and chat.tools:
                    config.actions = chat.tools
                yield config
        elif chat_entity is ChatEntity.AGENT_PRESET_BUILDER:
            instructions = await self._chat_entity_to_prompt(chat.entity_type, chat)
            tools = await build_agent_preset_builder_tools(chat.entity_id)
            try:
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions=instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=None,
                        custom_tools=tools,
                    )
            except TracecatNotFoundError as exc:
                raise ValueError(
                    "Agent preset builder requires a default AI model with valid provider credentials. "
                    "Configure the default model in Organization settings before chatting."
                ) from exc
        elif chat_entity is ChatEntity.COPILOT:
            entity_instructions = await self._chat_entity_to_prompt(
                chat.entity_type, chat
            )
            if chat.agent_preset_id:
                async with agent_svc.with_preset_config(
                    preset_id=chat.agent_preset_id
                ) as preset_config:
                    combined_instructions = (
                        f"{preset_config.instructions}\n\n{entity_instructions}"
                        if preset_config.instructions
                        else entity_instructions
                    )
                    config = replace(preset_config, instructions=combined_instructions)
                    if not config.actions and chat.tools:
                        config.actions = chat.tools
                    yield config
            else:
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions=entity_instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=chat.tools,
                    )
        else:
            raise ValueError(
                f"Unsupported chat entity type: {chat.entity_type}. Expected one of: {list(ChatEntity)}"
            )

    async def run_chat_turn(
        self,
        chat_id: uuid.UUID,
        request: ChatRequest | ContinueRunRequest,
        executor: BaseAgentExecutor,
    ) -> ChatResponse | None:
        """Run a chat turn, handling both start and continuation cases.

        This unified method handles both starting a new chat turn and continuing
        a chat turn after collecting approval decisions. It uses pattern matching
        to determine the request type and processes accordingly.

        Args:
            chat_id: The ID of the chat to run.
            request: Either a ChatRequest (start) or ContinueRunRequest (continue).
            executor: The agent executor to use for running the turn.

        Returns:
            ChatResponse if starting a new turn, None if continuing.

        Raises:
            TracecatNotFoundError: If the chat is not found.
            ValueError: If the request type or chat entity type is unsupported.
        """
        # Get the chat
        chat = await self.get_chat(chat_id)
        if not chat:
            raise TracecatNotFoundError(f"Chat with ID {chat_id} not found")

        # Determine if this is a start or continue request
        user_prompt: str | None = None
        deferred_tool_results: DeferredToolResults | None = None
        is_continuation = False

        match request:
            case ContinueRunRequest(decisions=decisions):
                # Continuation: build DeferredToolResults and log decisions
                is_continuation = True
                approvals_map = {
                    d.tool_call_id: d.to_deferred_result() for d in decisions
                }
                deferred_tool_results = DeferredToolResults(approvals=approvals_map)
            case VercelChatRequest(message=ui_message):
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

            case BasicChatRequest(message=prompt):
                user_prompt = prompt

            case _:
                raise ValueError(f"Unsupported request type: {type(request)}")

        if user_prompt is not None:
            logger.info("Received user prompt", prompt_length=len(user_prompt))

        # Build agent config using shared helper
        async with self._build_agent_config(chat) as config:
            # Prepare RunAgentArgs
            args = RunAgentArgs(
                user_prompt=user_prompt or "",
                session_id=chat_id,
                config=config,
                deferred_tool_results=deferred_tool_results,
                is_continuation=is_continuation,
            )
            await executor.start(args)

        # Return ChatResponse only for start requests
        if not is_continuation:
            stream_url = f"/api/chat/{chat_id}/stream"
            return ChatResponse(stream_url=stream_url, chat_id=chat_id)
        return None

    async def start_chat_turn(
        self,
        chat_id: uuid.UUID,
        request: ChatRequest,
        executor: BaseAgentExecutor,
    ) -> ChatResponse:
        """Start a new chat turn with an AI agent.

        This method is a convenience wrapper around `run_chat_turn` for starting
        a new chat turn. It supports both simple text messages and Vercel UI messages.

        Args:
            chat_id: The ID of the chat to start.
            request: The chat request (BasicChatRequest or VercelChatRequest).
            executor: The agent executor to use.

        Returns:
            ChatResponse with stream URL and chat ID.
        """
        result = await self.run_chat_turn(chat_id, request, executor)
        if result is None:
            raise ValueError("Expected ChatResponse but got None")
        return result

    async def continue_chat_turn(
        self,
        chat_id: uuid.UUID,
        request: ContinueRunRequest,
        executor: BaseAgentExecutor,
    ) -> None:
        """Continue a chat turn after collecting approval decisions.

        This method is a convenience wrapper around `run_chat_turn` for continuing
        a chat turn with deferred tool results.

        Args:
            chat_id: The ID of the chat to continue.
            request: The continuation request containing approval decisions.
            executor: The agent executor to use.

        Raises:
            TracecatNotFoundError: If the chat is not found.
            ValueError: If the chat entity type is unsupported.
        """
        await self.run_chat_turn(chat_id, request, executor)

    async def get_chat(
        self, chat_id: uuid.UUID, *, with_messages: bool = False
    ) -> Chat | None:
        """Get a chat by ID, ensuring it belongs to the current workspace."""
        stmt = select(Chat).where(
            Chat.id == chat_id,
            Chat.workspace_id == self.workspace_id,
        )
        if with_messages:
            stmt = stmt.options(selectinload(Chat.messages))
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_chats(
        self,
        *,
        user_id: UserID,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
    ) -> Sequence[Chat]:
        """List chats for the current workspace with optional entity filtering."""

        stmt = select(Chat).where(Chat.workspace_id == self.role.workspace_id)
        if user_id:
            stmt = stmt.where(Chat.user_id == user_id)

        if entity_type:
            stmt = stmt.where(Chat.entity_type == entity_type)

        if entity_id:
            stmt = stmt.where(Chat.entity_id == entity_id)

        stmt = stmt.order_by(Chat.created_at.desc()).limit(limit)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    @audit_log(resource_type="chat", action="update")
    async def update_chat(
        self,
        chat: Chat,
        params: ChatUpdate,
    ) -> Chat:
        """Update chat properties."""
        set_fields = params.model_dump(exclude_unset=True)

        if "agent_preset_id" in set_fields:
            preset_id = set_fields.pop("agent_preset_id")
            if preset_id is not None:
                preset_service = AgentPresetService(self.session, self.role)
                if not await preset_service.get_preset(preset_id):
                    raise TracecatNotFoundError(
                        f"Agent preset with ID '{preset_id}' not found"
                    )
            chat.agent_preset_id = preset_id

        # Update remaining fields if provided
        for field, value in set_fields.items():
            setattr(chat, field, value)
        self.session.add(chat)
        await self.session.commit()
        await self.session.refresh(chat)

        return chat

    async def update_chat_last_stream_id(self, chat: Chat, last_stream_id: str) -> Chat:
        """Update the last stream ID for a chat."""
        chat.last_stream_id = last_stream_id
        self.session.add(chat)
        await self.session.commit()
        await self.session.refresh(chat)
        return chat

    async def append_message(
        self,
        chat_id: uuid.UUID,
        message: ChatMessage,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> DBChatMessage:
        """Persist a message to the database."""
        db_message = DBChatMessage(
            chat_id=chat_id,
            kind=kind.value,
            workspace_id=self.workspace_id,
            data=ModelMessageTA.dump_python(message.message, mode="json")
            if isinstance(message.message, (ModelRequest, ModelResponse))
            else ClaudeMessageTA.dump_python(message.message, mode="json"),
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
        messages: Sequence[ChatMessage],
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None:
        """Persist multiple messages to the database in a single transaction."""
        if not messages:
            return

        # Create all DB message objects at once
        db_messages = []
        for message in messages:
            # Handle both ChatMessage wrappers and raw ModelMessage (backward compat)
            if isinstance(message, ChatMessage):
                raw_message = message.message
            else:
                raw_message = message

            # Store payload directly (version inferred on load from structure)
            if isinstance(raw_message, (ModelRequest, ModelResponse)):
                payload = ModelMessageTA.dump_python(raw_message, mode="json")
            else:
                payload = ClaudeMessageTA.dump_python(raw_message, mode="json")

            db_messages.append(
                DBChatMessage(
                    chat_id=chat_id,
                    kind=kind.value,
                    workspace_id=self.workspace_id,
                    data=payload,
                )
            )

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
        *,
        kinds: Sequence[MessageKind] | None = None,
    ) -> list[ModelMessage]:
        """Retrieve chat messages, optionally filtered by message kind."""
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

        messages: list[ModelMessage] = []
        for db_msg in db_messages:
            data = db_msg.data or {}
            if (
                isinstance(data, dict)
                and "schema_version" in data
                and "payload" in data
            ):
                schema_version = data.get("schema_version", "v1")
                payload = data.get("payload")
            else:
                schema_version = "v1"
                payload = data

            if schema_version != "v1":
                logger.debug(
                    "Skipping non-v1 message when listing messages",
                    message_id=db_msg.id,
                    schema_version=schema_version,
                )
                continue

            validated_msg = ModelMessageTA.validate_python(payload)
            messages.append(validated_msg)
        return messages

    async def list_all_messages(
        self,
        chat_id: uuid.UUID,
        *,
        kinds: Sequence[MessageKind] | None = None,
    ) -> list[ChatMessage]:
        """Retrieve all chat messages (both v1 and v2) as ChatMessage wrappers."""
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

        messages: list[ChatMessage] = []
        for db_msg in db_messages:
            try:
                chat_msg = ChatMessage.from_db(db_msg)
                messages.append(chat_msg)
            except Exception as e:
                logger.warning(
                    "Failed to parse message, skipping",
                    message_id=db_msg.id,
                    error=str(e),
                )
                continue
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
