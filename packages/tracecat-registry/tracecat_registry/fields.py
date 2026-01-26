"""Registry field types for UI rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
