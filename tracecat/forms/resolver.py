"""Resolve a form config against a workflow input schema.

``resolve_form`` is the single place the form rules live. Save, validate,
render, submit and git pull all call it, because the published input schema
can change after a form is saved.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, get_args, get_origin

from lark.exceptions import LarkError

from tracecat import config as app_config
from tracecat.expressions.expectations import parse_type
from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.constants import MAX_FILES_PER_FIELD, MAX_SAFE_INTEGER
from tracecat.forms.enums import (
    FormAutofillSource,
    FormInputKind,
    FormIssueCode,
    FormIssueSeverity,
    FormWidget,
)
from tracecat.forms.schemas import (
    FormBlock,
    FormConfig,
    FormDividerBlock,
    FormDividerEntry,
    FormFieldBlock,
    FormFieldConfig,
    FormFieldEntry,
    FormFieldOption,
    FormHeadingBlock,
    FormHeadingEntry,
    FormIssue,
    FormNoticeBlock,
    FormNoticeEntry,
    FormScalar,
    FormTextBlock,
    FormTextEntry,
    FormVisibleIf,
)
from tracecat.forms.types import FormInputType, ResolveResult, WidgetSpec
from tracecat.forms.values import check_answer, is_empty_answer, is_unsafe_integer
from tracecat.registry.fields import type_drop_null

# ----- Widget registry -----

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

_LIST_KINDS = frozenset({_K.LIST_STR, _K.LIST_ENUM, _K.LIST_DICT})
_ENUM_KINDS = frozenset({_K.ENUM, _K.LIST_ENUM})
_CONTROLLER_KINDS = frozenset({_K.STR, _K.INT, _K.FLOAT, _K.BOOL, _K.ENUM})
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
    """Classify a workflow input type such as ``list[str] | None``.

    A type that does not parse is ``UNSUPPORTED``.
    """
    try:
        parsed = parse_type(type_string, field_name)
    except (ValueError, LarkError):
        return FormInputType(_K.UNSUPPORTED)
    return _classify(parsed)


# ----- Resolver -----

type IssueLoc = list[str | int]


def humanize_field_name(name: str) -> str:
    """Turn an input name such as ``for_someone_else`` into a label."""
    return name.replace("_", " ").strip().capitalize()


@dataclass(slots=True)
class _Resolution:
    """Working state of one ``resolve_form`` call."""

    expects: Mapping[str, ExpectedField]
    inputs: dict[str, FormInputType]
    blocks: list[FormBlock] = field(default_factory=list)
    fields: dict[str, FormFieldBlock] = field(default_factory=dict)
    hidden: list[str] = field(default_factory=list)
    autofill: dict[str, FormAutofillSource] = field(default_factory=dict)
    issues: list[FormIssue] = field(default_factory=list)

    def issue(
        self,
        code: FormIssueCode,
        message: str,
        loc: IssueLoc,
        severity: FormIssueSeverity = FormIssueSeverity.ERROR,
    ) -> None:
        self.issues.append(
            FormIssue(severity=severity, code=code, message=message, loc=loc)
        )


def resolve_form(
    config: FormConfig, expects: Mapping[str, ExpectedField]
) -> ResolveResult:
    """Resolve a form config against a workflow input schema.

    Args:
        config: The form's presentation config.
        expects: The workflow's input schema, in the author's order.

    Returns:
        The blocks to render, the inputs that are hidden or filled in by the
        server, and any issues. An issue of severity ``error`` means the form
        cannot be saved, rendered or submitted.
    """
    state = _Resolution(
        expects=expects,
        inputs={
            name: classify_input(expected.type, name)
            for name, expected in expects.items()
        },
    )
    seen: set[str] = set()
    for index, entry in enumerate(config.layout or []):
        match entry:
            case FormHeadingEntry():
                state.blocks.append(
                    FormHeadingBlock(type="heading", text=entry.heading)
                )
            case FormTextEntry():
                state.blocks.append(FormTextBlock(type="text", text=entry.text))
            case FormNoticeEntry():
                state.blocks.append(
                    FormNoticeBlock(type="notice", text=entry.notice, tone=entry.tone)
                )
            case FormDividerEntry():
                state.blocks.append(FormDividerBlock(type="divider"))
            case FormFieldEntry():
                loc: IssueLoc = ["layout", index, "field"]
                if entry.field not in expects:
                    state.issue(
                        FormIssueCode.UNKNOWN_FIELD,
                        f"'{entry.field}' is not a workflow input",
                        loc,
                    )
                elif entry.field in seen:
                    state.issue(
                        FormIssueCode.DUPLICATE_FIELD,
                        f"'{entry.field}' appears more than once",
                        loc,
                    )
                else:
                    seen.add(entry.field)
                    _resolve_field_entry(state, entry, ["layout", index])

    # Inputs the layout leaves out follow in schema order.
    for name in expects:
        if name not in seen:
            _resolve_field_entry(state, FormFieldEntry(field=name), ["layout"])

    return ResolveResult(
        blocks=state.blocks,
        hidden=state.hidden,
        autofill=state.autofill,
        issues=state.issues,
    )


def _resolve_field_entry(
    state: _Resolution, entry: FormFieldEntry, loc: IssueLoc
) -> None:
    name = entry.field
    expected = state.expects[name]
    kind = state.inputs[name].kind

    if entry.hidden:
        # A shown default is checked as an answer. A hidden one never is, but
        # every int default stays in range. Float inputs are unaffected.
        if (
            kind is _K.INT
            and expected.has_default()
            and is_unsafe_integer(expected.default)
        ):
            state.issue(
                FormIssueCode.INTEGER_OUT_OF_RANGE,
                f"The default of '{name}' is outside ±{MAX_SAFE_INTEGER:,}, the "
                "largest whole number a form holds exactly",
                loc,
            )
        if entry.autofill is not None:
            state.issue(
                FormIssueCode.HIDDEN_WITH_AUTOFILL,
                f"'{name}' cannot be both hidden and autofilled",
                [*loc, "autofill"],
            )
        if not expected.has_default():
            state.issue(
                FormIssueCode.CONDITIONAL_FIELD_REQUIRED,
                f"'{name}' is hidden, so the input needs a default",
                [*loc, "hidden"],
            )
        state.hidden.append(name)
        return

    if entry.autofill is not None:
        if entry.autofill is FormAutofillSource.REQUESTER:
            fillable = {_K.DICT, _K.ANY}
        else:
            fillable = {_K.STR, _K.ANY}
        if kind not in fillable:
            state.issue(
                FormIssueCode.AUTOFILL_TYPE_MISMATCH,
                f"'{entry.autofill}' cannot fill input '{name}' of type "
                f"'{expected.type}'",
                [*loc, "autofill"],
            )
        state.autofill[name] = entry.autofill
        return

    widget = entry.widget or DEFAULT_WIDGETS.get(kind)
    if widget is None:
        if expected.has_default():
            state.issue(
                FormIssueCode.UNRENDERED_OPTIONAL_INPUT,
                f"'{name}' of type '{expected.type}' has no widget and is not "
                "shown. It takes its default.",
                loc,
                FormIssueSeverity.WARNING,
            )
        else:
            state.issue(
                FormIssueCode.NO_WIDGET_FOR_TYPE,
                f"'{name}' of type '{expected.type}' has no widget. Give the "
                "input a default, an autofill or a widget.",
                loc,
            )
        return

    spec = WIDGETS[widget]
    if kind not in spec.accepts:
        state.issue(
            FormIssueCode.INCOMPATIBLE_WIDGET,
            f"Widget '{widget}' cannot render input '{name}' of type '{expected.type}'",
            [*loc, "widget"],
        )
        return

    multiple = kind in _LIST_KINDS
    if entry.placeholder is not None and not spec.placeholder:
        state.issue(
            FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
            f"Widget '{widget}' has no placeholder",
            [*loc, "placeholder"],
        )
    block = FormFieldBlock(
        type="field",
        name=name,
        label=entry.label or humanize_field_name(name),
        help=entry.help or expected.description,
        placeholder=entry.placeholder,
        widget=widget,
        required=not expected.has_default(),
        multiple=multiple,
        default=expected.default if expected.has_default() else None,
        options=_resolve_options(state, entry, widget, loc),
        visible_if=_resolve_visible_if(state, entry, loc),
        config=_resolve_file_config(state, entry, widget, multiple, loc),
    )
    # The page starts with the default as the answer, so it must be one the
    # server accepts. A user or file field starts empty instead, and the
    # server applies the default.
    if widget not in (FormWidget.USER, FormWidget.FILE) and not is_empty_answer(
        block.default
    ):
        if (problem := check_answer(block, expected, block.default)) is not None:
            state.issue(
                FormIssueCode.DEFAULT_INVALID,
                f"The default of '{name}', {block.default!r}, is not a valid "
                f"answer: {problem[1]}",
                loc,
            )
    state.blocks.append(block)
    state.fields[name] = block


def _resolve_options(
    state: _Resolution, entry: FormFieldEntry, widget: FormWidget, loc: IssueLoc
) -> list[FormFieldOption] | None:
    input_type = state.inputs[entry.field]
    is_enum = input_type.kind in _ENUM_KINDS
    enum_options = [
        FormFieldOption(value=value, label=value) for value in input_type.enum_values
    ]

    if entry.options is None:
        if is_enum:
            return enum_options
        if WIDGETS[widget].options:
            state.issue(
                FormIssueCode.OPTIONS_REQUIRED,
                f"Widget '{widget}' needs options for input '{entry.field}'",
                loc,
            )
        return None

    if not WIDGETS[widget].options:
        state.issue(
            FormIssueCode.OPTIONS_NOT_SUPPORTED,
            f"Widget '{widget}' does not take options",
            [*loc, "options"],
        )
        return None

    options = [
        FormFieldOption(
            value=option.value,
            label=option.label or option.value,
            description=option.description,
            image=option.image,
        )
        for option in entry.options
    ]
    if not is_enum:
        return options

    # For an enum input, options only decorate the enum's values.
    for index, option in enumerate(entry.options):
        if option.value not in input_type.enum_values:
            state.issue(
                FormIssueCode.OPTION_NOT_IN_ENUM,
                f"'{option.value}' is not a value of input '{entry.field}'",
                [*loc, "options", index, "value"],
            )
    listed = {option.value for option in options}
    return options + [option for option in enum_options if option.value not in listed]


def _resolve_file_config(
    state: _Resolution,
    entry: FormFieldEntry,
    widget: FormWidget,
    multiple: bool,
    loc: IssueLoc,
) -> FormFieldConfig | None:
    if widget is not FormWidget.FILE:
        for key, value in (("accept", entry.accept), ("max_files", entry.max_files)):
            if value is not None:
                state.issue(
                    FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
                    f"Widget '{widget}' does not take '{key}'",
                    [*loc, key],
                )
        return None

    if not multiple and entry.max_files is not None and entry.max_files > 1:
        state.issue(
            FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
            f"Input '{entry.field}' takes one file. Use a list[dict] input for more.",
            [*loc, "max_files"],
        )
    return FormFieldConfig(
        accept=entry.accept,
        max_files=(entry.max_files or MAX_FILES_PER_FIELD) if multiple else 1,
        max_file_size=app_config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES,
    )


def _resolve_visible_if(
    state: _Resolution, entry: FormFieldEntry, loc: IssueLoc
) -> FormVisibleIf | None:
    if (rule := entry.visible_if) is None:
        return None

    rule_loc: IssueLoc = [*loc, "visible_if"]
    if not state.expects[entry.field].has_default():
        state.issue(
            FormIssueCode.CONDITIONAL_FIELD_REQUIRED,
            f"'{entry.field}' is conditional, so the input needs a default",
            rule_loc,
        )
    if rule.field not in state.expects:
        state.issue(
            FormIssueCode.VISIBLE_IF_UNKNOWN_FIELD,
            f"'{rule.field}' is not a workflow input",
            [*rule_loc, "field"],
        )
        return rule

    controller = state.fields.get(rule.field)
    kind = state.inputs[rule.field].kind
    if (
        controller is None
        or controller.visible_if is not None
        or kind not in _CONTROLLER_KINDS
    ):
        state.issue(
            FormIssueCode.VISIBLE_IF_INVALID_CONTROLLER,
            f"'{rule.field}' must be an earlier, unconditional field of type "
            "str, int, float, bool or enum",
            [*rule_loc, "field"],
        )
        return rule

    if is_unsafe_integer(rule.equals):
        state.issue(
            FormIssueCode.INTEGER_OUT_OF_RANGE,
            f"{rule.equals} is outside ±{MAX_SAFE_INTEGER:,}, the largest whole "
            "number a form holds exactly",
            [*rule_loc, "equals"],
        )
        return rule
    if not _is_controller_value(rule.equals, kind, controller):
        state.issue(
            FormIssueCode.VISIBLE_IF_INVALID_VALUE,
            f"'{rule.field}' can never equal {rule.equals!r}",
            [*rule_loc, "equals"],
        )
    return rule


def _is_controller_value(
    value: FormScalar, kind: FormInputKind, controller: FormFieldBlock
) -> bool:
    """Whether a ``visible_if`` value is one the controlling field can take."""
    match kind:
        case FormInputKind.BOOL:
            return isinstance(value, bool)
        case FormInputKind.INT:
            return isinstance(value, int) and not isinstance(value, bool)
        case FormInputKind.FLOAT:
            return isinstance(value, int | float) and not isinstance(value, bool)
        case _:
            if not isinstance(value, str):
                return False
            if controller.options is None:
                return True
            return any(option.value == value for option in controller.options)
