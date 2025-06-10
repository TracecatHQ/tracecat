import inspect
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Annotated, Literal, get_origin

from pydantic import Field, GetJsonSchemaHandler, RootModel
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from tracecat.logger import logger


class TracecatStrEnum(StrEnum):
    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core_schema: CoreSchema, _handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"enum": [m.value for m in cls], "type": "string"}


class ComponentID(StrEnum):
    """The ID of a component in the UI"""

    CODE = "code"
    SELECT = "select"
    TEXT_AREA = "text-area"
    SLIDER = "slider"
    TOGGLE = "toggle"
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    TAG_INPUT = "tag-input"
    YAML = "yaml"


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
class Slider(Component):
    """Render field as slider in UI"""

    component_id: Literal[ComponentID.SLIDER] = ComponentID.SLIDER
    min_val: float = float("-inf")
    max_val: float = float("inf")
    step: float = 1.0


@dataclass(slots=True)
class Integer(Component):
    """Render field as integer input"""

    component_id: Literal[ComponentID.INTEGER] = ComponentID.INTEGER
    min_val: int = 0
    max_val: int = 100
    step: int = 1


@dataclass(slots=True)
class Float(Component):
    """Render field as float input"""

    component_id: Literal[ComponentID.FLOAT] = ComponentID.FLOAT
    min_val: float = 0.0
    max_val: float = 100.0
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


DEFAULT_FIELD_BINDINGS: dict[type, type[Component]] = {
    int: Integer,
    float: Float,
    bool: Toggle,
    str: Text,
    list: TagInput,
    dict: Yaml,
}


def _safe_issubclass(cls: type, base: type) -> bool:
    try:
        return issubclass(cls, base)
    except Exception:
        return False


def get_default_component(param: inspect.Parameter) -> Component | None:
    field_type = param.annotation.__origin__
    if field_type is int:
        return Integer()
    elif field_type is float:
        return Float()
    elif field_type is bool:
        return Toggle()
    elif field_type is str:
        return Text()
    elif field_type is list | tuple:
        return TagInput()
    elif field_type is dict:
        return Yaml()
    elif get_origin(field_type) is Literal:
        if args := getattr(field_type, "__args__", None):
            return Select(options=list(args))
        else:
            raise ValueError("Couldn't get __args__ for Literal")
    elif _safe_issubclass(field_type, Enum):
        member_map = field_type._member_map_
        return Select(options=[member.value for member in member_map.values()])
    else:
        logger.warning(
            "Cannot get default component for {field_type}", field_type=field_type
        )
        return None


class EditorComponent(RootModel):
    root: Annotated[
        Text
        | Code
        | Select
        | TextArea
        | Slider
        | Integer
        | Float
        | Toggle
        | Yaml
        | TagInput,
        Field(discriminator="component_id"),
    ]
