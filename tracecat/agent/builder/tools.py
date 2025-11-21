"""Tools exposed to the agent preset builder assistant."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, TypedDict

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import Tool
from sqlalchemy import func, or_, select

from tracecat.agent.preset.schemas import AgentPresetRead, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.tools import build_agent_tools
from tracecat.chat.schemas import ChatMessage, ChatRead, ChatReadMinimal
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import RegistryAction
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger

AGENT_PRESET_BUILDER_TOOL_NAMES = [
    "get_agent_preset_summary",
    "update_agent_preset",
    "list_chats",
    "get_chat",
]


@asynccontextmanager
async def _preset_service():
    role = ctx_role.get()
    if role is None:
        raise TracecatAuthorizationError(
            "Agent preset builder tools require an authenticated workspace role",
        )

    async with AgentPresetService.with_session(role=role) as service:
        yield service


@asynccontextmanager
async def _chat_service():
    from tracecat.chat.service import ChatService

    role = ctx_role.get()
    if role is None:
        raise TracecatAuthorizationError(
            "Agent preset builder tools require an authenticated workspace role",
        )

    async with ChatService.with_session(role=role) as service:
        yield service


class ListAvailableActions(BaseModel):
    query: str = Field(
        ...,
        description="The query to search for actions.",
        min_length=1,
        max_length=100,
    )


class AgentToolSummary(TypedDict):
    """Summary of an agent tool."""

    action_id: str
    description: str


async def build_agent_preset_builder_tools(
    preset_id: uuid.UUID,
) -> list[Tool[Any]]:
    """Create tool instances bound to a specific preset ID."""

    async def get_agent_preset_summary() -> AgentPresetRead:
        """Return the latest configuration for this agent preset, including tools and approval rules."""

        async with _preset_service() as service:
            if not (preset := await service.get_preset(preset_id)):
                raise TracecatNotFoundError(
                    f"Agent preset with ID '{preset_id}' not found"
                )
        return AgentPresetRead.model_validate(preset)

    async def list_available_agent_tools(
        params: ListAvailableActions,
    ) -> list[AgentToolSummary]:
        """Return the list of available actions in the registry."""
        # Do naive contains + ilike search on the name column
        async with get_async_session_context_manager() as session:
            stmt = select(RegistryAction).where(
                # TODO: Workspace RLS
                or_(
                    # Namespace and name are covered by the concat search below
                    func.concat(
                        RegistryAction.namespace, ".", RegistryAction.name
                    ).ilike(f"%{params.query}%"),
                    RegistryAction.description.ilike(f"%{params.query}%"),
                )
            )
            result = await session.execute(stmt)
            actions = result.scalars().all()
            logger.info(
                "Listed available actions",
                query_term=params.query,
                actions=actions,
                count=len(actions),
            )
            return [
                AgentToolSummary(action_id=ra.action, description=ra.description)
                for ra in actions
            ]

    async def update_agent_preset(
        params: AgentPresetUpdate,
    ) -> AgentPresetRead:
        """Patch selected fields on the agent preset and return the updated record.

        Only include fields you want to change - omit unchanged fields so they remain untouched.
        Supported fields include:
        - `instructions`: system prompt text.
        - `actions`: list of allowed tool/action identifiers.
        - `namespaces`: optional namespaces to scope dynamic tool discovery.
        - `tool_approvals`: map of `{tool_name: bool}` where `true` means auto-run with no approval and `false` requires manual approval.
        """

        if not params.model_fields_set:
            raise ValueError("Provide at least one field to update.")

        async with _preset_service() as service:
            if not (preset := await service.get_preset(preset_id)):
                raise TracecatNotFoundError(
                    f"Agent preset with ID '{preset_id}' not found"
                )
            try:
                updated = await service.update_preset(preset, params)
            except TracecatValidationError as error:
                # Surface builder validation issues to the model as retryable errors.
                raise ModelRetry(str(error)) from error
        return AgentPresetRead.model_validate(updated)

    async def list_chats(
        limit: int = 50,
    ) -> list[ChatReadMinimal]:
        """List chats where this agent preset is being used by end users.

        Args:
            limit: Maximum number of chats to return (default 50).
        """
        try:
            target_entity_type = "agent_preset"
            target_entity_id = str(preset_id)

            async with _chat_service() as service:
                if service.role.user_id is None:
                    raise ModelRetry("Unable to list chats: authentication required.")
                chats = await service.list_chats(
                    user_id=service.role.user_id,
                    entity_type=target_entity_type,
                    entity_id=target_entity_id,
                    limit=limit,
                )
                return [
                    ChatReadMinimal.model_validate(chat, from_attributes=True)
                    for chat in chats
                ]
        except ModelRetry:
            raise
        except Exception as e:
            logger.error("Failed to list chats", error=str(e), preset_id=str(preset_id))
            raise ModelRetry("Unable to list chats at this time.") from e

    async def get_chat(chat_id: str) -> ChatRead:
        """Get the full message history and metadata for a specific chat.

        Args:
            chat_id: The UUID of the chat to retrieve.
        """
        try:
            uuid_id = uuid.UUID(chat_id)
        except ValueError as e:
            raise ModelRetry(f"Invalid chat ID format: {chat_id}") from e

        try:
            async with _chat_service() as service:
                chat = await service.get_chat(uuid_id, with_messages=True)
                if not chat:
                    raise ModelRetry(f"Chat {chat_id} not found.")

                return ChatRead(
                    id=chat.id,
                    title=chat.title,
                    user_id=chat.user_id,
                    entity_type=chat.entity_type,
                    entity_id=chat.entity_id,
                    tools=chat.tools,
                    agent_preset_id=chat.agent_preset_id,
                    created_at=chat.created_at,
                    updated_at=chat.updated_at,
                    last_stream_id=chat.last_stream_id,
                    messages=[
                        ChatMessage.from_db(message) for message in chat.messages
                    ],
                )
        except ModelRetry:
            raise
        except Exception as e:
            logger.error("Failed to get chat", error=str(e), chat_id=chat_id)
            raise ModelRetry(f"Unable to retrieve chat {chat_id}.") from e

    # Tracecat tools
    build_tools_result = await build_agent_tools(
        actions=[
            "core.table.download",
            "core.table.get_table_metadata",
            "core.table.list_tables",
            "core.table.search_rows",
            "tools.exa.get_contents",
            "tools.exa.get_research",
            "tools.exa.list_research",
            "tools.exa.research",
        ]
    )

    return [
        # Tool names must match ^[a-zA-Z0-9_-]{1,128}$ for some providers (e.g. Anthropic)
        Tool(get_agent_preset_summary),
        Tool(update_agent_preset),
        Tool(list_available_agent_tools),
        Tool(list_chats),
        Tool(get_chat),
        *build_tools_result.tools,
    ]
