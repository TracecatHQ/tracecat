"""Persisted workspace chat configuration shapes."""

from typing import Literal, NotRequired, TypedDict


class ChatCapabilitySelectionData(TypedDict):
    mode: NotRequired[Literal["all", "selected", "none"]]
    selected: NotRequired[list[str]]


class WorkspaceChatSettingsData(TypedDict):
    tools: NotRequired[ChatCapabilitySelectionData]
    mcp: NotRequired[ChatCapabilitySelectionData]
    subagents: NotRequired[ChatCapabilitySelectionData]


class WorkspaceChatOverridesData(TypedDict):
    """Null lists inherit workspace limits; empty lists select nothing."""

    tools: list[str] | None
    mcp_integrations: list[str] | None
    subagents: NotRequired[list[str] | None]
