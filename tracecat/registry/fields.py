import datetime
import inspect
import types
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import Field, RootModel

from tracecat.logger import logger


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
    elif field_type is Any:
        return Yaml()  # Use Yaml editor for Any type to allow flexible input
    elif field_type is datetime.datetime:
        return Yaml()
    elif _safe_issubclass(field_type, Enum):
        member_map = field_type._member_map_
        return Select(options=[member.value for member in member_map.values()])

    # Try container types
    origin = get_origin(field_type)
    # Strip None from the type
    if origin in (list, tuple):
        # Special case: list[str] should use TagInput
        args = get_args(field_type)
        if len(args) == 1 and args[0] is str:
            return TagInput()
        return Yaml()  # Other list[T] types use Json
    elif origin is dict:
        return Yaml()  # Changed from KeyValue() to Json() for dict[K, V]
    elif origin is Literal:
        if args := getattr(field_type, "__args__", None):
            return Select(options=list(args))
        else:
            raise ValueError("Couldn't get __args__ for Literal")
    else:
        logger.warning(
            f"Unknown field type: {field_type}. Using Yaml editor for flexible input."
        )
        return Yaml()


def get_components_for_union_type(field_type: Any) -> list[Component]:
    """Generate multiple components for union types like str | list[str]"""
    if not type_is_union(field_type):
        # Not a union type, return single component
        component = get_default_component(field_type)
        return [component] if component else []

    args = get_args(field_type)

    # Special rule: If union contains both str and list[str], use TagInput
    non_none_args = [arg for arg in args if arg is not type(None)]
    has_str = str in non_none_args
    has_list_str = any(
        get_origin(arg) is list and get_args(arg) == (str,) for arg in non_none_args
    )

    if has_str and has_list_str:
        return [TagInput()]

    # Everything else is a Yaml
    return [Yaml()]


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
        | TagInput
        | ActionType
        | WorkflowAlias,
        Field(discriminator="component_id"),
    ]
