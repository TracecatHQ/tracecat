"""Widget registry: which widget renders which workflow input type."""

from datetime import datetime
from typing import Any, Literal, get_args, get_origin

from lark.exceptions import LarkError

from tracecat.expressions.expectations import parse_type
from tracecat.forms.enums import FormInputKind, FormWidget
from tracecat.forms.types import FormInputType, WidgetSpec
from tracecat.registry.fields import type_drop_null

_K = FormInputKind

WIDGETS: dict[FormWidget, WidgetSpec] = {
    FormWidget.TEXT: WidgetSpec(accepts=frozenset({_K.STR})),
    FormWidget.LONG_TEXT: WidgetSpec(accepts=frozenset({_K.STR})),
    FormWidget.NUMBER: WidgetSpec(accepts=frozenset({_K.INT, _K.FLOAT})),
    FormWidget.TOGGLE: WidgetSpec(accepts=frozenset({_K.BOOL}), placeholder=False),
    FormWidget.SELECT: WidgetSpec(accepts=frozenset({_K.ENUM, _K.STR}), options=True),
    FormWidget.MULTI_SELECT: WidgetSpec(
        accepts=frozenset({_K.LIST_ENUM, _K.LIST_STR}), options=True
    ),
    FormWidget.CARDS: WidgetSpec(
        accepts=frozenset({_K.ENUM, _K.STR, _K.LIST_ENUM, _K.LIST_STR}),
        options=True,
        placeholder=False,
    ),
    FormWidget.TAGS: WidgetSpec(accepts=frozenset({_K.LIST_STR})),
    FormWidget.DATE: WidgetSpec(accepts=frozenset({_K.STR, _K.DATETIME})),
    FormWidget.DATETIME: WidgetSpec(accepts=frozenset({_K.DATETIME, _K.STR})),
    FormWidget.USER: WidgetSpec(accepts=frozenset({_K.DICT, _K.LIST_DICT})),
    FormWidget.FILE: WidgetSpec(
        accepts=frozenset({_K.DICT, _K.LIST_DICT}), placeholder=False
    ),
}

DEFAULT_WIDGETS: dict[FormInputKind, FormWidget] = {
    _K.STR: FormWidget.TEXT,
    _K.INT: FormWidget.NUMBER,
    _K.FLOAT: FormWidget.NUMBER,
    _K.BOOL: FormWidget.TOGGLE,
    _K.ENUM: FormWidget.SELECT,
    _K.LIST_ENUM: FormWidget.MULTI_SELECT,
    _K.LIST_STR: FormWidget.TAGS,
    _K.DATETIME: FormWidget.DATETIME,
}
"""Widget used when the layout names none. Other kinds have no default."""

LIST_KINDS = frozenset({_K.LIST_STR, _K.LIST_ENUM, _K.LIST_DICT})
ENUM_KINDS = frozenset({_K.ENUM, _K.LIST_ENUM})
SCALAR_KINDS = frozenset({_K.STR, _K.INT, _K.FLOAT, _K.BOOL, _K.ENUM})
"""Kinds a ``visible_if`` rule can compare against."""

_PRIMITIVE_KINDS: dict[Any, FormInputKind] = {
    str: _K.STR,
    int: _K.INT,
    float: _K.FLOAT,
    bool: _K.BOOL,
    datetime: _K.DATETIME,
    Any: _K.ANY,
}
_LIST_ITEM_KINDS: dict[FormInputKind, FormInputKind] = {
    _K.STR: _K.LIST_STR,
    _K.ENUM: _K.LIST_ENUM,
    _K.DICT: _K.LIST_DICT,
}


def _classify(parsed: Any) -> FormInputType:
    parsed = type_drop_null(parsed)
    if (kind := _PRIMITIVE_KINDS.get(parsed)) is not None:
        return FormInputType(kind)
    origin = get_origin(parsed)
    if origin is Literal:
        return FormInputType(_K.ENUM, tuple(get_args(parsed)))
    if origin is dict:
        return FormInputType(_K.DICT)
    if origin is list:
        item = _classify(get_args(parsed)[0])
        if (kind := _LIST_ITEM_KINDS.get(item.kind)) is not None:
            return FormInputType(kind, item.enum_values)
    return FormInputType(_K.UNSUPPORTED)


def classify_input(type_string: str, field_name: str) -> FormInputType:
    """Classify a workflow input type for widget selection.

    Args:
        type_string: The input's type annotation, such as ``list[str] | None``.
        field_name: The input's name, used by the type parser.

    Returns:
        The input kind, with the allowed values for enum kinds. A type that
        does not parse is ``UNSUPPORTED``.
    """
    try:
        parsed = parse_type(type_string, field_name)
    except (ValueError, LarkError):
        return FormInputType(_K.UNSUPPORTED)
    return _classify(parsed)
