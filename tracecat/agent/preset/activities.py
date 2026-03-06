from __future__ import annotations

import uuid

from pydantic import BaseModel, model_validator
from temporalio import activity

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role


class ResolveAgentPresetConfigActivityInput(BaseModel):
    role: Role
    preset_slug: str | None = None
    preset_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def ensure_identifier(self) -> ResolveAgentPresetConfigActivityInput:
        if self.preset_slug is None and self.preset_id is None:
            raise ValueError("Either preset_slug or preset_id must be provided")
        return self


@activity.defn
async def resolve_agent_preset_config_activity(
    args: ResolveAgentPresetConfigActivityInput,
) -> AgentConfigPayload:
    async with AgentPresetService.with_session(role=args.role) as service:
        config = await service.resolve_agent_preset_config(
            preset_id=args.preset_id,
            slug=args.preset_slug,
        )
        return agent_config_to_payload(config)
