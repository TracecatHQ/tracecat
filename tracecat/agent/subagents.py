"""Shared types and helpers for Claude subagent configuration."""

from __future__ import annotations

import uuid
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
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
    preset_version: int | None = Field(default=None, ge=1)
    name: AgentAlias | None = Field(default=None)
    description: str | None = Field(default=None, max_length=1000)
    max_turns: int | None = Field(default=None, ge=1)

    @property
    def alias(self) -> str:
        """Effective runtime alias for this subagent."""
        return self.name or self.preset


class ResolvedAttachedSubagentRef(AttachedSubagentRef):
    """Persisted subagent ref with immutable preset/version identifiers."""

    preset_id: uuid.UUID
    preset_version_id: uuid.UUID
    preset_version: int | None = Field(default=None, ge=1)


type AnyAttachedSubagentRef = ResolvedAttachedSubagentRef | AttachedSubagentRef


class AgentsConfig(BaseModel):
    """User-facing agents toggle and optional preset-backed subagents."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    subagents: list[AnyAttachedSubagentRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_subagents_enabled(self) -> Self:
        if not self.enabled and self.subagents:
            raise ValueError("subagents require enabled=true")
        return self


class ResolvedAgentsConfig(BaseModel):
    """Persisted agents toggle with immutable resolved child refs."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    subagents: list[ResolvedAttachedSubagentRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_subagents_enabled(self) -> Self:
        if not self.enabled and self.subagents:
            raise ValueError("subagents require enabled=true")
        return self


def validate_subagent_alias(alias: str) -> None:
    """Reject aliases reserved by Claude or Tracecat runtime semantics."""
    if alias in RESERVED_SUBAGENT_ALIASES:
        raise ValueError(f"Subagent alias '{alias}' is reserved")
