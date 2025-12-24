"""Registry field types for UI rendering (registry-client compatible)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tracecat_registry import config

if not config.flags.registry_client or TYPE_CHECKING:
    from tracecat.registry.fields import ActionType, AgentPreset, Code, TextArea
else:

    @dataclass(slots=True)
    class TextArea:
        component_id: Literal["text-area"] = "text-area"
        rows: int = 4
        placeholder: str = ""

    @dataclass(slots=True)
    class Code:
        component_id: Literal["code"] = "code"
        lang: Literal["yaml", "python"] = "python"

    @dataclass(slots=True)
    class AgentPreset:
        component_id: Literal["agent-preset"] = "agent-preset"

    @dataclass(slots=True)
    class ActionType:
        component_id: Literal["action-type"] = "action-type"
        multiple: bool = False


__all__ = ["ActionType", "AgentPreset", "Code", "TextArea"]
