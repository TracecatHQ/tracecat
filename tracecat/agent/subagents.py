"""Shared types and helpers for Claude subagent configuration."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Any

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


def _drop_legacy_agents_config_keys(data: Any) -> Any:
    """Strip the legacy ``enabled`` key from persisted agents config payloads.

    ``enabled`` is a leftover from the removed agents toggle. It is still
    present on rows written before this release, and on rows written by an older
    replica during a rolling deploy, so it is stripped on read until a later
    contract migration removes it from stored rows. Everything else still hits
    ``extra=forbid``.
    """
    if isinstance(data, Mapping) and "enabled" in data:
        return {key: value for key, value in data.items() if key != "enabled"}
    return data


class AgentSubagentsConfig(BaseModel):
    """User-facing preset-backed subagents."""

    model_config = ConfigDict(extra="forbid")

    subagents: list[AnyAttachedSubagentRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_keys(cls, data: Any) -> Any:
        return _drop_legacy_agents_config_keys(data)


class ResolvedAgentsConfig(BaseModel):
    """Persisted immutable resolved child refs."""

    model_config = ConfigDict(extra="forbid")

    subagents: list[ResolvedAttachedSubagentRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_keys(cls, data: Any) -> Any:
        return _drop_legacy_agents_config_keys(data)


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
