from __future__ import annotations

import uuid

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


class CustomModelProviderConfigResult(BaseModel):
    model_name: str | None = None
    base_url: str
    passthrough: bool = False


@activity.defn
async def resolve_custom_model_provider_config_activity(
    role: Role,
) -> CustomModelProviderConfigResult:
    activity.logger.info("Resolving custom model provider config")
    async with AgentManagementService.with_session(role) as svc:
        creds = await svc.get_runtime_provider_credentials(
            "custom-model-provider",
        )
    if creds is None:
        activity.logger.error("Custom model provider credentials not found")
        raise ApplicationError("Invalid custom model provider credentials")
    if not (base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL")):
        activity.logger.error(
            "Custom model provider base URL missing",
            extra={
                "has_model_name_override": bool(
                    creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")
                ),
                "has_api_key": bool(creds.get("CUSTOM_MODEL_PROVIDER_API_KEY")),
            },
        )
        raise ApplicationError("Custom model provider base URL is required")
    passthrough = creds.get("CUSTOM_MODEL_PROVIDER_PASSTHROUGH", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    activity.logger.info(
        "Resolved custom model provider config",
        extra={
            "passthrough": passthrough,
            "has_model_name_override": bool(
                creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")
            ),
            "has_api_key": bool(creds.get("CUSTOM_MODEL_PROVIDER_API_KEY")),
            "has_base_url": bool(base_url),
        },
    )
    return CustomModelProviderConfigResult(
        base_url=base_url,
        model_name=creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"),
        passthrough=passthrough,
    )
