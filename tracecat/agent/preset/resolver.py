"""Shared resolver for preset-backed subagent configurations."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
    has_manual_tool_approvals,
    validate_subagent_alias,
)
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.exceptions import TracecatValidationError

if TYPE_CHECKING:
    from tracecat.agent.types import AgentConfig
    from tracecat.db.models import AgentPreset, AgentPresetVersion


class AgentPresetResolutionService(Protocol):
    def resolve_agent_preset_version(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> Awaitable[AgentPresetVersion]: ...

    def get_preset(self, preset_id: uuid.UUID) -> Awaitable[AgentPreset | None]: ...

    def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> Awaitable[AgentConfig]: ...


class ResolvedSubagentConfig(BaseModel):
    """Runtime-ready resolved subagent configuration."""

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


class ResolvedSubagentResolution(BaseModel):
    """Resolved subagent binding with optional runtime payload fields."""

    binding: ResolvedAttachedSubagentRef
    description: str | None = None
    prompt: str | None = None
    config: AgentConfigPayload | None = None

    def require_runtime_config(self) -> ResolvedSubagentConfig:
        if self.description is None or self.prompt is None or self.config is None:
            raise ValueError("Resolved subagent is missing runtime configuration")
        return ResolvedSubagentConfig(
            binding=self.binding,
            description=self.description,
            prompt=self.prompt,
            config=self.config,
        )


class ResolvedAgentsConfigResult(BaseModel):
    """Resolved preset-backed subagent bindings."""

    enabled: bool = False
    subagents: list[ResolvedSubagentResolution] = Field(default_factory=list)

    def to_agents_binding(self) -> ResolvedAgentsConfig:
        return ResolvedAgentsConfig(
            enabled=self.enabled,
            subagents=[subagent.binding for subagent in self.subagents],
        )

    def to_runtime_config(self) -> ResolvedAgentsRuntimeConfig:
        return ResolvedAgentsRuntimeConfig(
            enabled=self.enabled,
            subagents=[
                subagent.require_runtime_config() for subagent in self.subagents
            ],
        )


class ResolvedAgentsRuntimeConfig(BaseModel):
    """Runtime-ready resolved preset-backed subagent config."""

    enabled: bool = False
    subagents: list[ResolvedSubagentConfig] = Field(default_factory=list)

    def to_agents_binding(self) -> ResolvedAgentsConfig:
        return ResolvedAgentsConfig(
            enabled=self.enabled,
            subagents=[subagent.binding for subagent in self.subagents],
        )


async def resolve_agents_config(
    service: AgentPresetResolutionService,
    *,
    agents: AgentSubagentsConfig | dict[str, Any] | None,
    parent_preset_id: uuid.UUID | None = None,
    parent_slug: str | None = None,
    include_runtime_config: bool = False,
) -> ResolvedAgentsConfigResult:
    """Resolve and validate preset-backed subagent refs."""

    config = AgentSubagentsConfig.model_validate({} if agents is None else agents)
    if not config.enabled:
        return ResolvedAgentsConfigResult()

    aliases: set[str] = set()
    resolved_subagents: list[ResolvedSubagentResolution] = []
    for ref in config.subagents:
        alias = ref.alias
        try:
            validate_subagent_alias(alias)
        except ValueError as err:
            raise TracecatValidationError(str(err)) from err
        if alias in aliases:
            raise TracecatValidationError(
                f"Duplicate subagent alias '{alias}' in agents config"
            )
        aliases.add(alias)

        preset_version_id = getattr(ref, "preset_version_id", None)
        if preset_version_id is not None:
            version = await service.resolve_agent_preset_version(
                preset_version_id=preset_version_id,
            )
        else:
            version = await service.resolve_agent_preset_version(
                slug=ref.preset,
                preset_version=ref.preset_version,
            )
        references_parent_id = (
            parent_preset_id is not None and version.preset_id == parent_preset_id
        )
        references_parent_slug = (
            preset_version_id is None
            and parent_slug is not None
            and ref.preset == parent_slug
        )
        if references_parent_id or references_parent_slug:
            raise TracecatValidationError("Agent presets cannot reference themselves")

        child_agents = AgentSubagentsConfig.model_validate(version.agents)
        if child_agents.enabled:
            raise TracecatValidationError(
                f"Subagent preset '{ref.preset}' cannot define its own agents in v1"
            )
        if has_manual_tool_approvals(version.tool_approvals):
            raise TracecatValidationError(
                f"Subagent preset '{ref.preset}' uses manual approvals, "
                "which are not supported for subagents yet."
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
        if include_runtime_config:
            preset = await service.get_preset(version.preset_id)
            child_config = await service.resolve_agent_preset_config(
                preset_version_id=version.id,
            )
            description = (
                ref.description
                or (preset.description if preset is not None else None)
                or f"Use for tasks assigned to the {alias} specialist."
            )
            resolved_subagents.append(
                ResolvedSubagentResolution(
                    binding=binding,
                    description=description,
                    prompt=build_subagent_prompt(child_config.instructions),
                    config=agent_config_to_payload(child_config),
                )
            )
        else:
            resolved_subagents.append(ResolvedSubagentResolution(binding=binding))

    return ResolvedAgentsConfigResult(enabled=True, subagents=resolved_subagents)


def build_subagent_prompt(instructions: str | None) -> str:
    base = (
        "If asked about your identity, you are a Tracecat automation subagent. "
        "Complete only the delegated subtask and return a concise final result to the parent agent."
    )
    return f"{base}\n\n{instructions}" if instructions else base
