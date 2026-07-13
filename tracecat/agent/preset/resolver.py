"""Shared resolver for preset-backed subagent configurations."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field

from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    AttachedSubagentRef,
    HeadAttachedSubagentRef,
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

type SkippedAgentPresetRefReason = Literal["deleted", "unpublished", "not_found"]


class AgentPresetResolutionService(Protocol):
    def resolve_agent_preset_version(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
        include_deleted_preset: bool = False,
    ) -> Awaitable[AgentPresetVersion]: ...

    def get_preset(
        self, preset_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Awaitable[AgentPreset | None]: ...

    def resolve_agent_preset_version_for_subagent_ref(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> Awaitable[AgentPresetVersion | SkippedAgentPresetRef]: ...

    def resolve_agent_preset_version_snapshot(
        self,
        *,
        preset_version_id: uuid.UUID,
        preset_id: uuid.UUID | None = None,
        preset_slug: str | None = None,
    ) -> Awaitable[AgentPresetVersion | SkippedAgentPresetRef]: ...

    def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
        include_deleted_preset: bool = False,
    ) -> Awaitable[AgentConfig]: ...

    def _get_version_agents_config(
        self, version: AgentPresetVersion
    ) -> Awaitable[AgentSubagentsConfig]: ...


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


class SkippedAgentPresetRef(BaseModel):
    """Preset-backed subagent ref intentionally omitted from resolution."""

    preset_id: uuid.UUID | None = None
    preset_slug: str | None = None
    reason: SkippedAgentPresetRefReason


class ResolvedAgentsConfigResult(BaseModel):
    """Resolved preset-backed subagent bindings."""

    enabled: bool = False
    subagents: list[ResolvedSubagentResolution] = Field(default_factory=list)
    skipped: list[SkippedAgentPresetRef] = Field(default_factory=list)

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
    preserve_resolved_versions: bool = False,
) -> ResolvedAgentsConfigResult:
    """Resolve and validate preset-backed subagent refs.

    ``preserve_resolved_versions`` restores a run snapshot to its exact stored
    ``preset_version_id``. Fresh resolution always follows ResourceHead edges
    to each child preset's current version.
    """

    # NOTE(ENG-1526): preserve_resolved_versions exists so resumed sessions can
    # rebuild their preserved binding verbatim (the session activity fails the
    # run on any binding mismatch). PR 2.3a moves resolution to dispatch time
    # with per-turn session bindings, which may subsume or remove this flag —
    # revisit when dispatch-time manifests fully own resume reconstruction.

    config = AgentSubagentsConfig.model_validate({} if agents is None else agents)
    if not config.enabled:
        return ResolvedAgentsConfigResult()

    aliases: set[str] = set()
    resolved_subagents: list[ResolvedSubagentResolution] = []
    skipped: list[SkippedAgentPresetRef] = []
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

        # Resolved refs carry immutable preset/version UUIDs; unresolved refs
        # only carry a slug + optional version int. Matching narrows the union
        # so we read the right identifiers without runtime getattr() lookups.
        # Order matters: ResolvedAttachedSubagentRef subclasses
        # AttachedSubagentRef, so the resolved case must come first or resolved
        # refs would silently fall into the base-class branch.
        match ref:
            case ResolvedAttachedSubagentRef(
                preset_id=preset_id, preset_version_id=preset_version_id
            ):
                if preserve_resolved_versions:
                    # Resumed-session restore: resolve the exact stored version
                    # verbatim. A child preset that advanced or was soft-deleted
                    # mid-session must not change an in-flight session's topology.
                    version_or_skip = (
                        await service.resolve_agent_preset_version_snapshot(
                            preset_version_id=preset_version_id,
                            preset_id=preset_id,
                            preset_slug=ref.preset,
                        )
                    )
                else:
                    version_or_skip = (
                        await service.resolve_agent_preset_version_for_subagent_ref(
                            preset_id=preset_id,
                        )
                    )
            case HeadAttachedSubagentRef(preset_id=preset_id):
                # Authored and persisted edges follow the stable child head.
                version_or_skip = (
                    await service.resolve_agent_preset_version_for_subagent_ref(
                        preset_id=preset_id,
                    )
                )
            case AttachedSubagentRef(preset=preset_slug):
                # Boundary refs without UUIDs resolve by the current live slug.
                version_or_skip = (
                    await service.resolve_agent_preset_version_for_subagent_ref(
                        slug=preset_slug,
                    )
                )
        if isinstance(version_or_skip, SkippedAgentPresetRef):
            skipped.append(version_or_skip)
            continue
        version = version_or_skip
        references_parent_id = (
            parent_preset_id is not None and version.preset_id == parent_preset_id
        )
        references_parent_slug = (
            not isinstance(ref, HeadAttachedSubagentRef)
            and parent_slug is not None
            and ref.preset == parent_slug
        )
        if references_parent_id or references_parent_slug:
            raise TracecatValidationError("Agent presets cannot reference themselves")

        # Check the same store the runtime executes from: version subagent
        # config is edge-authoritative, so validating the legacy JSON here
        # would let JSON-disabled/edges-enabled drift slip nested subagents
        # past the v1 ban. The shallow read (no ref resolution) also avoids
        # recursing through the child's own subagent refs.
        child_agents = await service._get_version_agents_config(version)
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
            # On the resumed-session restore path a soft-deleted child preset
            # must still enrich the runtime config (with_deleted read).
            preset = await service.get_preset(
                version.preset_id, include_deleted=preserve_resolved_versions
            )
            child_config = await service.resolve_agent_preset_config(
                preset_version_id=version.id,
                include_deleted_preset=preserve_resolved_versions,
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

    return ResolvedAgentsConfigResult(
        enabled=True,
        subagents=resolved_subagents,
        skipped=skipped,
    )


def build_subagent_prompt(instructions: str | None) -> str:
    base = (
        "If asked about your identity, you are a Tracecat automation subagent. "
        "Complete only the delegated subtask and return a concise final result to the parent agent."
    )
    return f"{base}\n\n{instructions}" if instructions else base
