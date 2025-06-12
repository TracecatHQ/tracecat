import inspect
import types
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Annotated, Any, Literal, Optional, Union, get_args, get_origin

from pydantic import Field, GetJsonSchemaHandler, RootModel
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


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
    TOGGLE = "toggle"
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    TAG_INPUT = "tag-input"
    KEY_VALUE = "key-value"
    YAML = "yaml"
    JSON = "json"


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


@dataclass(slots=True)
class Json(Component):
    """Render field as JSON editor in UI"""

    component_id: Literal[ComponentID.JSON] = ComponentID.JSON


@dataclass(slots=True)
class KeyValue(Component):
    """Render field as key-value editor in UI"""

    component_id: Literal[ComponentID.KEY_VALUE] = ComponentID.KEY_VALUE
    key_placeholder: str = "Key"
    value_placeholder: str = "Value"


def _safe_issubclass(cls: type, base: type) -> bool:
    try:
        return issubclass(cls, base)
    except Exception:
        return False


def type_is_union(type_hint: Any) -> bool:
    """Determines if a type annotation is a union type."""
    return isinstance(type_hint, types.UnionType) or get_origin(type_hint) is Union


def type_is_nullable(type_hint: Any) -> bool:
    """Determines if a type annotation allows None as a valid value.

    A type is considered nullable if it explicitly includes None as a possible value,
    either through the Union operator (|) or Optional type. This is distinct from
    optional parameters which may or may not be present in a function call.

    Examples:
        >>> is_nullable(str | None)
        True
        >>> is_nullable(Optional[str])
        True
        >>> is_nullable(str)
        False

    Args:
        typ: The type annotation to check for nullability.

    Returns:
        True if the type can be None, False otherwise.
    """
    if type_is_union(type_hint):
        # Handle T | None / Optional[T]
        return type(None) in get_args(type_hint)
    return False


def type_drop_null(type_hint: Any) -> Any:
    """Remove NoneType from a union type annotation"""

    # Handle Python 3.10+ union syntax (|)
    if type_is_union(type_hint):
        args = get_args(type_hint)
    else:
        # it's not a union type
        return type_hint
    non_none_args = sorted((arg for arg in args if arg is not type(None)), key=str)

    if len(non_none_args) == 0:
        return type(None)
    elif len(non_none_args) == 1:
        return non_none_args[0]
    else:
        # Reconstruct union
        result, *rest = non_none_args
        for arg in rest:
            result = result | arg
        return result


def is_optional(param: inspect.Parameter) -> bool:
    """Determines if a parameter has a default value.

    A parameter is considered optional if it has a default value assigned to it in the function
    signature. This is distinct from nullable types which may or may not be None.

    Examples:
        >>> def func(x: str = "default"): pass
        >>> is_optional(inspect.signature(func).parameters["x"])
        True
        >>> def func(x: str): pass
        >>> is_optional(inspect.signature(func).parameters["x"])
        False

    Args:
        param: The parameter to check for optionality.

    Returns:
        True if the parameter has a default value, False otherwise.
    """
    return param.default is not param.empty


def get_default_component(field_type: Any) -> Component | None:
    # This returns T in Annotated[T, ...]
    if field_type is int:
        return Integer()
    elif field_type is float:
        return Float()
    elif field_type is bool:
        return Toggle()
    elif field_type is str:
        return Text()
    elif _safe_issubclass(field_type, Enum):
        member_map = field_type._member_map_
        return Select(options=[member.value for member in member_map.values()])

    # Try container types
    origin = get_origin(field_type)
    # Strip None from the type
    if origin in (list, tuple):
        return TagInput()
    elif origin is dict:
        return KeyValue()
    elif origin is Literal:
        if args := getattr(field_type, "__args__", None):
            return Select(options=list(args))
        else:
            raise ValueError("Couldn't get __args__ for Literal")
    else:
        return None


def get_components_for_union_type(field_type: Any) -> list[Component]:
    """Generate multiple components for union types like str | list[str]"""
    if not type_is_union(field_type):
        # Not a union type, return single component
        component = get_default_component(field_type)
        return [component] if component else []

    args = get_args(field_type)
    components = []

    for arg in args:
        if arg is type(None):
            continue  # Skip None type

        component = get_default_component(arg)
        if component and component not in components:
            components.append(component)

    return components


class EditorComponent(RootModel):
    root: Annotated[
        Text
        | Code
        | Select
        | TextArea
        | Integer
        | Float
        | Toggle
        | Yaml
        | Json
        | KeyValue
        | TagInput,
        Field(discriminator="component_id"),
    ]


if __name__ == "__main__":
    t = list[str] | None
    y = Optional[list[str]]  # noqa: UP007
    z = list[str]
    print(type_is_nullable(t))
    print(type_is_nullable(y))
    print(type_is_nullable(z))

    # Test it
    T1 = list[str] | None
    T2 = Union[int, str, None]  # noqa: UP007
    T3 = str | int | None

    print(type_drop_null(T1))  # list[str]
    print(type_drop_null(T2))  # typing.Union[int, str]
    print(type_drop_null(T3))  # str | int

    t = list[str] | str
    for a in t.__args__:
        print(get_default_component(a))
