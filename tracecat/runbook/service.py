"""Runbook service."""

import uuid
from datetime import datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.agent.service import AgentManagementService
from tracecat.cases.service import CasesService
from tracecat.chat.models import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Case, Chat, Runbook
from tracecat.runbook.flows import generate_runbook_from_chat
from tracecat.runbook.models import RunbookCreate
from tracecat.runbook.prompts import NEW_RUNBOOK_INSTRUCTIONS
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


class RunbookService(BaseWorkspaceService):
    """Runbook service."""

    service_name = "runbook"

    def __init__(
        self, session: AsyncSession, role: Role, case_id: uuid.UUID | None = None
    ):
        super().__init__(session, role)
        self.chats = ChatService(session, role)
        self.cases = CasesService(session, role)
        self.agent = AgentManagementService(session, role)
        self.case_id = case_id

    async def _get_chat(self, chat_id: uuid.UUID) -> Chat:
        """Get a chat by ID."""
        chat = await self.chats.get_chat(chat_id)
        if not chat:
            raise TracecatNotFoundError(f"Chat with ID {chat_id} not found")
        return chat

    async def _get_chat_messages(self, chat: Chat) -> list[ChatMessage]:
        """Get a chat by ID."""
        messages = await self.chats.get_chat_messages(chat)
        if not messages or len(messages) == 0:
            raise TracecatNotFoundError(f"Chat {chat.id} has no messages")
        return messages

    async def _get_case(self, case_id: uuid.UUID) -> Case:
        """Get a case by ID."""
        case = await self.cases.get_case(case_id)
        if not case:
            raise TracecatNotFoundError(f"Case with ID {case_id} not found")
        return case

    async def create_runbook(self, params: RunbookCreate):
        """Create a runbook from a case or from scratch."""

        if self.case_id and params.chat_id:
            return await self._create_runbook_from_case(
                chat_id=params.chat_id,
                alias=params.alias,
            )
        else:
            return await self._create_runbook(alias=params.alias)

    async def _create_runbook(
        self,
        title: str | None = None,
        instructions: str | None = None,
        tools: list[str] | None = None,
        alias: str | None = None,
    ):
        """Create a runbook from scratch."""
        default_title = f"New runbook - {datetime.now().isoformat()}"
        runbook = Runbook(
            owner_id=self.workspace_id,
            title=title or default_title,
            instructions=instructions or NEW_RUNBOOK_INSTRUCTIONS,
            tools=tools or [],
            alias=alias,
        )
        self.session.add(runbook)
        await self.session.commit()
        await self.session.refresh(runbook)
        return runbook

    async def _create_runbook_from_case(
        self, chat_id: uuid.UUID, alias: str | None = None
    ):
        """Create a runbook from a case."""
        if self.case_id is None:
            raise ValueError("Case ID is required to create a runbook from a case")

        case = await self._get_case(self.case_id)
        chat = await self._get_chat(chat_id)
        messages = await self._get_chat_messages(chat)
        title, tools = chat.title, chat.tools

        # Generate runbook from chat via LLM agent
        instructions = await generate_runbook_from_chat(
            case=case,
            messages=messages,
            tools=tools,
            session=self.session,
            role=self.role,
        )

        # Create runbook
        runbook = await self._create_runbook(
            title=title,
            instructions=instructions,
            tools=chat.tools,
            alias=alias,
        )
        return runbook
