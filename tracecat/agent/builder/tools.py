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

    # Tracecat tools
    build_tools_result = await build_agent_tools(
        actions=[
            "core.table.download",
            "core.table.get_table_metadata",
            "core.table.list_tables",
            "core.table.search_rows",
            "tools.exa.research",
            "tools.exa.get_research",
            "tools.exa.list_research",
        ]
    )

    return [
        # Tool names must match ^[a-zA-Z0-9_-]{1,128}$ for some providers (e.g. Anthropic)
        Tool(get_agent_preset_summary),
        Tool(update_agent_preset),
        Tool(list_available_agent_tools),
        *build_tools_result.tools,
    ]
