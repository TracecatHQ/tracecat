from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class ComponentID(StrEnum):
    """The ID of a component in the UI"""

    CODE = "code"
    SELECT = "select"
    TEXT_AREA = "text-area"
    TOGGLE = "toggle"
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    TAG_INPUT = "tag-input"
    YAML = "yaml"
    ACTION_TYPE = "action-type"
    WORKFLOW_ALIAS = "workflow-alias"


@dataclass(slots=True)
class Component: ...


@dataclass(slots=True)
class Text(Component):
    """Base class for fields"""

    component_id: Literal[ComponentID.TEXT] = ComponentID.TEXT


@dataclass(slots=True)
class Code(Component):
    """Render field as code block in UI"""

    component_id: Literal[ComponentID.CODE] = ComponentID.CODE
    lang: Literal["yaml", "python"] = "python"


@dataclass(slots=True)
class Select(Component):
    """Render field as dropdown in UI"""

    component_id: Literal[ComponentID.SELECT] = ComponentID.SELECT
    options: list[str] | None = None
    multiple: bool = False


@dataclass(slots=True)
class TagInput(Component):
    """Render field as tag input in UI"""

    component_id: Literal[ComponentID.TAG_INPUT] = ComponentID.TAG_INPUT


@dataclass(slots=True)
class TextArea(Component):
    """Render field as textarea in UI"""

    component_id: Literal[ComponentID.TEXT_AREA] = ComponentID.TEXT_AREA
    rows: int = 4
    placeholder: str = ""


@dataclass(slots=True)
class Integer(Component):
    """Render field as integer input"""

    component_id: Literal[ComponentID.INTEGER] = ComponentID.INTEGER
    min_val: int | None = None
    max_val: int | None = None
    step: int = 1


@dataclass(slots=True)
class Float(Component):
    """Render field as float input"""

    component_id: Literal[ComponentID.FLOAT] = ComponentID.FLOAT
    min_val: float | None = None
    max_val: float | None = None
    step: float = 0.1


@dataclass(slots=True)
class Toggle(Component):
    """Render field as toggle switch"""

    label_on: str = "On"
    label_off: str = "Off"
    component_id: Literal[ComponentID.TOGGLE] = ComponentID.TOGGLE


@dataclass(slots=True)
class Yaml(Component):
    """Render field as YAML editor in UI"""

    component_id: Literal[ComponentID.YAML] = ComponentID.YAML


@dataclass(slots=True)
class ActionType(Component):
    """Render field as action type dropdown in UI"""

    component_id: Literal[ComponentID.ACTION_TYPE] = ComponentID.ACTION_TYPE
    multiple: bool = False


@dataclass(slots=True)
class WorkflowAlias(Component):
    """Render field as workflow alias dropdown in UI"""

    component_id: Literal[ComponentID.WORKFLOW_ALIAS] = ComponentID.WORKFLOW_ALIAS
