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
        name: str | None = None,
        slug: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        model_name: str | None = None,
        model_provider: str | None = None,
        base_url: str | None = None,
        output_type: dict[str, Any] | str | None = None,
        actions: list[str] | None = None,
        namespaces: list[str] | None = None,
        tool_approvals: dict[str, bool] | None = None,
        mcp_server_url: str | None = None,
        mcp_server_headers: dict[str, str] | None = None,
        model_settings: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        """Patch selected fields on the agent preset and return the updated record."""

        update_payload = {
            key: value
            for key, value in {
                "name": name,
                "slug": slug,
                "description": description,
                "instructions": instructions,
                "model_name": model_name,
                "model_provider": model_provider,
                "base_url": base_url,
                "output_type": output_type,
                "actions": actions,
                "namespaces": namespaces,
                "tool_approvals": tool_approvals,
                "mcp_server_url": mcp_server_url,
                "mcp_server_headers": mcp_server_headers,
                "model_settings": model_settings,
                "retries": retries,
            }.items()
            if value is not None
        }

        if not update_payload:
            raise ValueError("Provide at least one field to update.")

        update = AgentPresetUpdate(**update_payload)
        async with _preset_service() as service:
            await service.update_preset(preset_id, update)
            preset = await service.get_preset(preset_id)
        return preset.model_dump(mode="json")

    return [
        Tool(
            get_agent_preset_summary, name="get_agent_preset_summary", takes_ctx=False
        ),
        Tool(update_agent_preset, name="update_agent_preset", takes_ctx=False),
    ]
