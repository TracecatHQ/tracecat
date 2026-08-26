"""Shared types and helpers for Claude subagent configuration."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

AgentAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    ),
]
_AGENT_ALIAS_ADAPTER = TypeAdapter(AgentAlias)

PresetRef = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]

RESERVED_SUBAGENT_ALIASES = frozenset(
    {
        "agent",
        "general-purpose",
        "root",
        "task",
    }
)


class AttachedSubagentRef(BaseModel):
    """User-facing reference to a preset-backed subagent."""

    model_config = ConfigDict(extra="forbid")

    preset: PresetRef
    name: AgentAlias | None = Field(default=None)
    description: str | None = Field(default=None, max_length=1000)
    max_turns: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_version_selector(cls, data: object) -> object:
        """Ignore the retired authored selector without weakening extra checks."""

        if (
            isinstance(data, Mapping)
            and "preset_version" in data
            and "preset_version_id" not in data
        ):
            normalized = dict(data)
            normalized.pop("preset_version", None)
            return normalized
        return data

    @property
    def alias(self) -> str:
        """Effective runtime alias for this subagent."""
        return self.name or self.preset


class HeadAttachedSubagentRef(AttachedSubagentRef):
    """Stable internal reference to a child preset ResourceHead."""

    preset_id: uuid.UUID


class ResolvedAttachedSubagentRef(HeadAttachedSubagentRef):
    """Persisted subagent ref with immutable preset/version identifiers."""

    preset_version_id: uuid.UUID
    preset_version: int | None = Field(default=None, ge=1)


type CompatibleAttachedSubagentRef = (
    ResolvedAttachedSubagentRef | HeadAttachedSubagentRef | AttachedSubagentRef
)


class _AgentsConfig[SubagentRefT: AttachedSubagentRef](BaseModel):
    """Common enabled-state validation for a single subagent ref state."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    subagents: list[SubagentRefT] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_other_config_state(cls, data: object) -> object:
        """Revalidate another state model through this model's strict fields."""
        if isinstance(data, _AgentsConfig) and not isinstance(data, cls):
            return data.model_dump(mode="json")
        return data

    @model_validator(mode="after")
    def validate_subagents_enabled(self) -> Self:
        if not self.enabled and self.subagents:
            raise ValueError("subagents require enabled=true")
        return self


class AuthoredAgentsConfig(_AgentsConfig[AttachedSubagentRef]):
    """User-authored subagent refs that have not resolved to ResourceHeads."""


class HeadAgentsConfig(_AgentsConfig[HeadAttachedSubagentRef]):
    """Normalized subagent refs bound to stable child ResourceHeads."""


class ResolvedAgentsConfig(_AgentsConfig[ResolvedAttachedSubagentRef]):
    """Persisted agents toggle with immutable resolved child refs."""

    def to_head_config(self) -> HeadAgentsConfig:
        """Drop exact-version fields after resolving normalized head edges."""
        return HeadAgentsConfig(
            enabled=self.enabled,
            subagents=[
                HeadAttachedSubagentRef(
                    preset=subagent.preset,
                    preset_id=subagent.preset_id,
                    name=subagent.name,
                    description=subagent.description,
                    max_turns=subagent.max_turns,
                )
                for subagent in self.subagents
            ],
        )


class AgentSubagentsConfig(_AgentsConfig[CompatibleAttachedSubagentRef]):
    """Mixed-version compatibility parser for authored and persisted refs.

    New state-specific boundaries should use :class:`AuthoredAgentsConfig`,
    :class:`HeadAgentsConfig`, or :class:`ResolvedAgentsConfig` instead.
    """


def validate_subagent_alias(alias: str) -> None:
    """Reject aliases reserved by Claude or Tracecat runtime semantics."""
    try:
        normalized_alias = _AGENT_ALIAS_ADAPTER.validate_python(alias)
    except ValidationError as err:
        raise ValueError(
            f"Invalid subagent alias '{alias}'. Use lowercase letters, numbers, "
            "and hyphens; start and end with a letter or number."
        ) from err
    if normalized_alias in RESERVED_SUBAGENT_ALIASES:
        raise ValueError(f"Subagent alias '{normalized_alias}' is reserved")


def has_manual_tool_approvals(
    tool_approvals: Mapping[str, bool] | None,
) -> bool:
    """Return whether a preset has tools that require manual approval."""
    return any(
        requires_approval is True
        for requires_approval in (tool_approvals or {}).values()
    )
