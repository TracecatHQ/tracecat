"""Runbook service."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.agent.service import AgentManagementService
from tracecat.cases.service import CasesService
from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Case, Chat, Runbook
from tracecat.runbook.flows import (
    execute_runbook_on_case,
    generate_runbook_from_chat,
    generate_runbook_title_from_chat,
)
from tracecat.runbook.models import RunbookCreate, RunbookExecuteResponse, RunbookUpdate
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
        """Return the persisted messages for the given chat."""
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

        if params.chat_id is None:
            return await self._create_runbook(alias=params.alias)

        chat = await self._get_chat(params.chat_id)

        if self.case_id is not None:
            case_id = self.case_id
        elif chat.entity_type == ChatEntity.CASE:
            case_id = chat.entity_id
        else:
            raise ValueError(
                "chat_id refers to a chat that is not linked to a case; provide case_id"
            )

        case = await self._get_case(case_id)
        messages = await self._get_chat_messages(chat)
        return await self._create_runbook_from_case(
            case=case,
            chat=chat,
            messages=messages,
            alias=params.alias,
        )

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

        # Re-fetch with relationships loaded
        loaded_runbook = await self.get_runbook_by_id(runbook.id)
        if not loaded_runbook:
            # This should never happen since we just created it
            raise ValueError(f"Failed to load newly created runbook {runbook.id}")
        return loaded_runbook

    async def _create_runbook_from_case(
        self,
        *,
        case: Case,
        chat: Chat,
        messages: list[ChatMessage],
        alias: str | None = None,
    ):
        """Create a runbook using chat history tied to a case."""

        tools = chat.tools

        # Generate runbook from chat via LLM agent
        instructions = await generate_runbook_from_chat(
            case=case,
            messages=messages,
            tools=tools,
            session=self.session,
            role=self.role,
        )

        # Generate runbook title from chat via LLM agent
        title = await generate_runbook_title_from_chat(
            case=case,
            messages=messages,
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

    def _is_uuid(self, value: str) -> bool:
        """Check if a string is a valid UUID."""
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False

    async def get_runbook(self, runbook_id_or_alias: str) -> Runbook | None:
        """Get a runbook by ID or alias."""
        if self._is_uuid(runbook_id_or_alias):
            return await self.get_runbook_by_id(uuid.UUID(runbook_id_or_alias))
        else:
            return await self.get_runbook_by_alias(runbook_id_or_alias)

    async def get_runbook_by_id(self, runbook_id: uuid.UUID) -> Runbook | None:
        """Get a runbook by ID."""
        stmt = (
            select(Runbook)
            .where(
                Runbook.id == runbook_id,
                Runbook.owner_id == self.workspace_id,
            )
            .options(selectinload(Runbook.related_cases))  # type: ignore[arg-type]
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def get_runbook_by_alias(self, alias: str) -> Runbook | None:
        """Get a runbook by alias."""
        stmt = (
            select(Runbook)
            .where(
                Runbook.alias == alias,
                Runbook.owner_id == self.workspace_id,
            )
            .options(selectinload(Runbook.related_cases))  # type: ignore[arg-type]
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def list_runbooks(
        self, limit: int = 50, sort_by: str = "created_at", order: str = "desc"
    ) -> Sequence[Runbook]:
        """List runbooks."""
        # Determine the sort column
        if sort_by not in ["created_at", "updated_at"]:
            raise ValueError("Invalid sort by field")

        if sort_by == "created_at":
            sort_column = col(Runbook.created_at)
        else:
            sort_column = col(Runbook.updated_at)

        if order == "desc":
            sort_column = sort_column.desc()
        else:
            sort_column = sort_column.asc()

        stmt = (
            select(Runbook)
            .where(Runbook.owner_id == self.workspace_id)
            .options(selectinload(Runbook.related_cases))  # type: ignore[arg-type]
            .order_by(sort_column)
            .limit(limit)
        )

        result = await self.session.exec(stmt)
        return result.all()

    async def update_runbook(self, runbook: Runbook, params: RunbookUpdate) -> Runbook:
        """Update a runbook."""
        set_fields = params.model_dump(exclude_unset=True)
        for key, value in set_fields.items():
            setattr(runbook, key, value)
        self.session.add(runbook)
        await self.session.commit()
        await self.session.refresh(runbook)

        # Re-fetch with relationships loaded
        loaded_runbook = await self.get_runbook_by_id(runbook.id)
        if not loaded_runbook:
            # This should never happen since we just updated it
            raise ValueError(f"Failed to load updated runbook {runbook.id}")
        return loaded_runbook

    async def delete_runbook(self, runbook: Runbook) -> None:
        """Delete a runbook."""
        await self.session.delete(runbook)
        await self.session.commit()

    async def execute_runbook(
        self, runbook: Runbook, case_ids: list[uuid.UUID]
    ) -> RunbookExecuteResponse:
        """Execute a runbook across multiple cases and return per-case stream URLs."""
        stream_urls = {}
        for case_id in case_ids:
            case = await self._get_case(case_id)
            stream_url = await execute_runbook_on_case(
                runbook=runbook,
                case=case,
                session=self.session,
                role=self.role,
            )
            stream_urls[str(case_id)] = stream_url
        return RunbookExecuteResponse(stream_urls=stream_urls)
