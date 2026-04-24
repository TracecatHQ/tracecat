from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, model_validator
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.subagents import (
    AgentsConfig,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
    validate_subagent_alias,
)
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


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


class ResolvedSubagentConfig(BaseModel):
    binding: ResolvedAttachedSubagentRef
    description: str
    prompt: str
    config: AgentConfigPayload

    @property
    def alias(self) -> str:
        return self.binding.alias

    @property
    def max_turns(self) -> int | None:
        return self.binding.max_turns


class ResolveAgentsConfigActivityInput(BaseModel):
    role: Role
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    parent_preset_id: uuid.UUID | None = None


class ResolveAgentsConfigActivityResult(BaseModel):
    enabled: bool = False
    subagents: list[ResolvedSubagentConfig] = Field(default_factory=list)

    def to_agents_binding(self) -> ResolvedAgentsConfig:
        return ResolvedAgentsConfig(
            enabled=self.enabled,
            subagents=[subagent.binding for subagent in self.subagents],
        )


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


@activity.defn
async def resolve_agents_config_activity(
    args: ResolveAgentsConfigActivityInput,
) -> ResolveAgentsConfigActivityResult:
    config = args.agents
    if not config.enabled:
        return ResolveAgentsConfigActivityResult()

    aliases: set[str] = set()
    subagents: list[ResolvedSubagentConfig] = []

    async with AgentPresetService.with_session(role=args.role) as service:
        for ref in config.subagents:
            alias = ref.alias
            try:
                validate_subagent_alias(alias)
            except ValueError as err:
                raise TracecatValidationError(str(err)) from err
            if alias in aliases:
                raise TracecatValidationError(f"Duplicate subagent alias '{alias}'")
            aliases.add(alias)

            preset_version_id = getattr(ref, "preset_version_id", None)
            version = await service.resolve_agent_preset_version(
                slug=ref.preset,
                preset_version_id=preset_version_id,
                preset_version=ref.preset_version,
            )
            if (
                args.parent_preset_id is not None
                and version.preset_id == args.parent_preset_id
            ):
                raise TracecatValidationError(
                    "Agent presets cannot reference themselves"
                )
            child_agents = AgentsConfig.model_validate(version.agents)
            if child_agents.enabled:
                raise TracecatValidationError(
                    f"Subagent preset '{ref.preset}' cannot define its own agents in v1"
                )

            preset = await service.get_preset(version.preset_id)
            child_config = await service.resolve_agent_preset_config(
                preset_version_id=version.id
            )
            binding = ResolvedAttachedSubagentRef(
                preset=ref.preset,
                preset_version=version.version,
                name=ref.name,
                description=ref.description,
                max_turns=ref.max_turns,
                preset_id=version.preset_id,
                preset_version_id=version.id,
            )

            description = (
                ref.description
                or (preset.description if preset is not None else None)
                or f"Use for tasks assigned to the {alias} specialist."
            )
            prompt = _build_subagent_prompt(child_config.instructions)
            subagents.append(
                ResolvedSubagentConfig(
                    binding=binding,
                    description=description,
                    prompt=prompt,
                    config=agent_config_to_payload(child_config),
                )
            )

    return ResolveAgentsConfigActivityResult(
        enabled=True,
        subagents=subagents,
    )


def _build_subagent_prompt(instructions: str | None) -> str:
    base = (
        "If asked about your identity, you are a Tracecat automation subagent. "
        "Complete only the delegated subtask and return a concise final result to the parent agent."
    )
    return f"{base}\n\n{instructions}" if instructions else base


class CustomModelProviderConfigResult(BaseModel):
    model_name: str | None = None
    base_url: str
    passthrough: bool = False


@activity.defn
async def resolve_custom_model_provider_config_activity(
    role: Role,
    use_workspace_credentials: bool,
) -> CustomModelProviderConfigResult:
    activity.logger.info(
        "Resolving custom model provider config",
        extra={"use_workspace_credentials": use_workspace_credentials},
    )
    async with AgentManagementService.with_session(role) as svc:
        creds = await svc.get_runtime_provider_credentials(
            "custom-model-provider",
            use_workspace_credentials=use_workspace_credentials,
        )
    if creds is None:
        activity.logger.error(
            "Custom model provider credentials not found",
            extra={"use_workspace_credentials": use_workspace_credentials},
        )
        raise ApplicationError("Invalid custom model provider credentials")
    if not (base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL")):
        activity.logger.error(
            "Custom model provider base URL missing",
            extra={
                "use_workspace_credentials": use_workspace_credentials,
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
            "use_workspace_credentials": use_workspace_credentials,
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
