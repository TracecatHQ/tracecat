"""Tools exposed to the agent preset builder assistant."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TypedDict

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry
from pydantic_ai.tools import Tool as PATool
from sqlalchemy import func, or_, select

from tracecat.agent.preset.schemas import AgentPresetRead, AgentPresetUpdate
from tracecat.agent.runtime.pydantic_ai.adapter import to_pydantic_ai_tools
from tracecat.agent.session.schemas import (
    AgentSessionRead,
    AgentSessionReadWithMessages,
)
from tracecat.agent.tools import build_agent_tools
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
    "list_sessions",
    "get_session",
]


@asynccontextmanager
async def _preset_service():
    from tracecat.agent.preset.service import AgentPresetService

    role = ctx_role.get()
    if role is None:
        raise TracecatAuthorizationError(
            "Agent preset builder tools require an authenticated workspace role",
        )

    async with AgentPresetService.with_session(role=role) as service:
        yield service


@asynccontextmanager
async def _session_service():
    from tracecat.agent.session.service import AgentSessionService

    role = ctx_role.get()
    if role is None:
        raise TracecatAuthorizationError(
            "Agent preset builder tools require an authenticated workspace role",
        )

    async with AgentSessionService.with_session(role=role) as service:
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
) -> list[PATool]:
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

    async def list_sessions(
        limit: int = 50,
    ) -> list[AgentSessionRead]:
        """List agent sessions where this agent preset is being used by end users.

        Args:
            limit: Maximum number of sessions to return (default 50).
        """
        try:
            from tracecat.agent.session.types import AgentSessionEntity

            async with _session_service() as service:
                if service.role.user_id is None:
                    raise ModelRetry(
                        "Unable to list sessions: authentication required."
                    )
                sessions = await service.list_sessions(
                    user_id=service.role.user_id,
                    entity_type=AgentSessionEntity.AGENT_PRESET,
                    entity_id=preset_id,
                    limit=limit,
                )
                return [
                    AgentSessionRead.model_validate(session, from_attributes=True)
                    for session in sessions
                ]
        except ModelRetry:
            raise
        except Exception as e:
            logger.error(
                "Failed to list sessions", error=str(e), preset_id=str(preset_id)
            )
            raise ModelRetry("Unable to list sessions at this time.") from e

    async def get_session(session_id: str) -> AgentSessionReadWithMessages:
        """Get the full message history and metadata for a specific agent session.

        Args:
            session_id: The UUID of the session to retrieve.
        """

        try:
            async with _session_service() as service:
                session = await service.get_session(uuid.UUID(session_id))
                if not session or str(session.entity_id) != str(preset_id):
                    raise ModelRetry(f"Session {session_id} not found.")

                messages = await service.list_messages(session.id)

                return AgentSessionReadWithMessages(
                    id=session.id,
                    workspace_id=session.workspace_id,
                    title=session.title,
                    user_id=session.user_id,
                    entity_type=session.entity_type,
                    entity_id=session.entity_id,
                    tools=session.tools,
                    agent_preset_id=session.agent_preset_id,
                    harness_type=session.harness_type,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    last_stream_id=session.last_stream_id,
                    messages=messages,
                )
        except ModelRetry:
            raise
        except Exception as e:
            logger.error("Failed to get session", error=str(e), session_id=session_id)
            raise ModelRetry(f"Unable to retrieve session {session_id}.") from e

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
    # Convert Tracecat Tools to pydantic-ai Tools
    pa_tools = to_pydantic_ai_tools(build_tools_result.tools)

    return [
        # Tool names must match ^[a-zA-Z0-9_-]{1,128}$ for some providers (e.g. Anthropic)
        PATool(get_agent_preset_summary),
        PATool(update_agent_preset),
        PATool(list_available_agent_tools),
        PATool(list_sessions),
        PATool(get_session),
        *pa_tools,
    ]
