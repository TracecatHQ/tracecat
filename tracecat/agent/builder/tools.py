"""Tools exposed to the agent preset builder assistant."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai.tools import Tool

from tracecat.agent.preset.schemas import AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatAuthorizationError

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


def build_agent_preset_builder_tools(
    preset_id: uuid.UUID,
) -> list[Tool[Any]]:
    """Create tool instances bound to a specific preset ID."""

    async def get_agent_preset_summary() -> dict[str, Any]:
        """Return the latest configuration for this agent preset."""

        async with _preset_service() as service:
            preset = await service.get_preset(preset_id)
        return preset.model_dump(mode="json")

    async def update_agent_preset(
        params: AgentPresetUpdate,
    ) -> dict[str, Any]:
        """Patch selected fields on the agent preset and return the updated record.

        Only include fields you want to change - omit unchanged fields to minimize data transfer.
        """

        if not params.model_fields_set:
            raise ValueError("Provide at least one field to update.")

        async with _preset_service() as service:
            updated = await service.update_preset(preset_id, params)
        return updated.model_dump(mode="json")

    return [
        Tool(
            get_agent_preset_summary, name="get_agent_preset_summary", takes_ctx=False
        ),
        Tool(update_agent_preset, name="update_agent_preset", takes_ctx=False),
    ]
