"""Internal types for agent preset services."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from tracecat.agent.skill.types import SkillMcpGrant


class SkillBindingSpec(NamedTuple):
    """Comparable key for one skill binding on a preset head."""

    skill_id: uuid.UUID
    skill_version_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PresetToolSource:
    """A tool declaration and its origin, retained through policy evaluation."""

    tool_id: str
    skill_id: uuid.UUID | None = None
    skill_name: str | None = None


@dataclass(frozen=True, slots=True)
class PresetToolInputs:
    """Authored inputs and exact skill versions for one policy evaluation."""

    key: uuid.UUID
    actions: Sequence[str]
    namespaces: Sequence[str]
    mcp_integrations: Sequence[str]
    tool_approvals: Mapping[str, bool]
    skill_version_ids: Sequence[uuid.UUID]


@dataclass(frozen=True, slots=True)
class EffectivePresetTools:
    """Combined tool grants after applying source-independent preset policies."""

    actions: tuple[str, ...]
    mcp_grants: tuple[SkillMcpGrant, ...]
    tool_approvals: dict[str, bool]
    requires_internet_access: bool
    blocked_tools: tuple[PresetToolSource, ...]
    internet_sources: tuple[PresetToolSource, ...]
