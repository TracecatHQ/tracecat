"""Registry field types for UI rendering."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
class AgentModel:
    component_id: Literal["agent-model"] = "agent-model"


@dataclass(slots=True)
class ActionType:
    component_id: Literal["action-type"] = "action-type"
    multiple: bool = False


class ModelSelection(BaseModel):
    """Model dropdown selection passed into registry actions.

    The AgentModel dropdown resolves a user's pick into these three values so
    the runtime can locate credentials and the invocation target. Templates
    receive a single ``model: ModelSelection`` kwarg instead of parallel
    ``model_name`` / ``model_provider`` / ``catalog_id`` kwargs.
    """

    model_config = ConfigDict(extra="ignore")

    model_name: str
    model_provider: str
    catalog_id: uuid.UUID | None = None


__all__ = [
    "ActionType",
    "AgentModel",
    "AgentPreset",
    "Code",
    "ModelSelection",
    "TextArea",
]
