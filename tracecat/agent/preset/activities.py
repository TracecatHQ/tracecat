from __future__ import annotations

import uuid

from loguru import logger
from pydantic import BaseModel, model_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role


class ResolveAgentPresetConfigActivityInput(BaseModel):
    role: Role
    preset_slug: str | None = None
    preset_id: uuid.UUID | None = None
    preset_version_id: uuid.UUID | None = None
    preset_version: int | None = None

    @model_validator(mode="after")
    def ensure_identifier(self) -> ResolveAgentPresetConfigActivityInput:
        if (
            self.preset_slug is None
            and self.preset_id is None
            and self.preset_version_id is None
        ):
            raise ValueError(
                "Either preset_slug, preset_id, or preset_version_id must be provided"
            )
        return self


class ResolveAgentPresetVersionRefActivityInput(BaseModel):
    role: Role
    preset_slug: str
    preset_version: int | None = None


class AgentPresetVersionRef(BaseModel):
    preset_id: uuid.UUID
    preset_version_id: uuid.UUID


@activity.defn
async def resolve_agent_preset_config_activity(
    args: ResolveAgentPresetConfigActivityInput,
) -> AgentConfigPayload:
    async with AgentPresetService.with_session(role=args.role) as service:
        config = await service.resolve_agent_preset_config(
            preset_id=args.preset_id,
            slug=args.preset_slug,
            preset_version_id=args.preset_version_id,
            preset_version=args.preset_version,
        )
        return agent_config_to_payload(config)


@activity.defn
async def resolve_agent_preset_version_ref_activity(
    args: ResolveAgentPresetVersionRefActivityInput,
) -> AgentPresetVersionRef:
    async with AgentPresetService.with_session(role=args.role) as service:
        version = await service.resolve_agent_preset_version(
            slug=args.preset_slug,
            preset_version=args.preset_version,
        )
        return AgentPresetVersionRef(
            preset_id=version.preset_id,
            preset_version_id=version.id,
        )


class LitellmConfigResult(BaseModel):
    model_name: str | None = None
    base_url: str


@activity.defn
async def resolve_litellm_config_activity(
    role: Role, use_workspace_credentials: bool
) -> LitellmConfigResult:
    async with AgentManagementService.with_session(role) as svc:
        creds = await svc.get_runtime_provider_credentials(
            "litellm", use_workspace_credentials=use_workspace_credentials
        )
    if creds is None:
        raise ApplicationError("Invalid litellm credentials")
    logger.warning("Litellm config", creds=creds)
    return LitellmConfigResult(
        base_url=creds["LITELLM_BASE_URL"], model_name=creds.get("LITELLM_MODEL_NAME")
    )
