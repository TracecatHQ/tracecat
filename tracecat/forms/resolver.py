"""Resolve a form config against a workflow input schema.

``resolve_form`` is the single place the form rules live. Save, preview,
render, submit and git pull all call it, because the published input schema
can change after a form is saved.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from tracecat import config as app_config
from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.constants import MAX_FILES_PER_FIELD
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
from tracecat.forms.types import FormInputType, ResolveResult
from tracecat.forms.widgets import (
    DEFAULT_WIDGETS,
    ENUM_KINDS,
    LIST_KINDS,
    SCALAR_KINDS,
    WIDGETS,
    classify_input,
)

type IssueLoc = list[str | int]

_REQUESTER_KINDS = frozenset({FormInputKind.DICT, FormInputKind.ANY})
_TEXT_KINDS = frozenset({FormInputKind.STR, FormInputKind.ANY})


def _autofill_kinds(source: FormAutofillSource) -> frozenset[FormInputKind]:
    """Input kinds an autofill source can fill."""
    if source is FormAutofillSource.REQUESTER:
        return _REQUESTER_KINDS
    return _TEXT_KINDS


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

    def error(self, code: FormIssueCode, message: str, loc: IssueLoc) -> None:
        self.issues.append(
            FormIssue(
                severity=FormIssueSeverity.ERROR, code=code, message=message, loc=loc
            )
        )

    def warning(self, code: FormIssueCode, message: str, loc: IssueLoc) -> None:
        self.issues.append(
            FormIssue(
                severity=FormIssueSeverity.WARNING, code=code, message=message, loc=loc
            )
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
                loc: IssueLoc = ["layout", index]
                if entry.field not in expects:
                    state.error(
                        FormIssueCode.UNKNOWN_FIELD,
                        f"'{entry.field}' is not a workflow input",
                        [*loc, "field"],
                    )
                    continue
                if entry.field in seen:
                    state.error(
                        FormIssueCode.DUPLICATE_FIELD,
                        f"'{entry.field}' appears more than once",
                        [*loc, "field"],
                    )
                    continue
                seen.add(entry.field)
                _resolve_field_entry(state, entry, loc)

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
        if not expected.has_default():
            state.error(
                FormIssueCode.CONDITIONAL_FIELD_REQUIRED,
                f"'{name}' is hidden, so the input needs a default",
                [*loc, "hidden"],
            )
        state.hidden.append(name)
        return

    if entry.autofill is not None:
        if kind not in _autofill_kinds(entry.autofill):
            state.error(
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
            state.warning(
                FormIssueCode.UNRENDERED_OPTIONAL_INPUT,
                f"'{name}' of type '{expected.type}' has no widget and is not "
                "shown. It takes its default.",
                loc,
            )
        else:
            state.error(
                FormIssueCode.NO_WIDGET_FOR_TYPE,
                f"'{name}' of type '{expected.type}' has no widget. Give the "
                "input a default, an autofill or a widget.",
                loc,
            )
        return

    spec = WIDGETS[widget]
    if kind not in spec.accepts:
        state.error(
            FormIssueCode.INCOMPATIBLE_WIDGET,
            f"Widget '{widget}' cannot render input '{name}' of type '{expected.type}'",
            [*loc, "widget"],
        )
        return

    multiple = kind in LIST_KINDS
    if entry.placeholder is not None and not spec.placeholder:
        state.error(
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
    state.blocks.append(block)
    state.fields[name] = block


def _resolve_options(
    state: _Resolution, entry: FormFieldEntry, widget: FormWidget, loc: IssueLoc
) -> list[FormFieldOption] | None:
    input_type = state.inputs[entry.field]
    is_enum = input_type.kind in ENUM_KINDS
    takes_options = WIDGETS[widget].options

    if entry.options is None:
        if is_enum:
            return [
                FormFieldOption(value=value, label=value)
                for value in input_type.enum_values
            ]
        if takes_options:
            state.error(
                FormIssueCode.OPTIONS_REQUIRED,
                f"Widget '{widget}' needs options for input '{entry.field}'",
                loc,
            )
        return None

    if not takes_options:
        state.error(
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
            state.error(
                FormIssueCode.OPTION_NOT_IN_ENUM,
                f"'{option.value}' is not a value of input '{entry.field}'",
                [*loc, "options", index, "value"],
            )
    listed = {option.value for option in options}
    return options + [
        FormFieldOption(value=value, label=value)
        for value in input_type.enum_values
        if value not in listed
    ]


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
                state.error(
                    FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
                    f"Widget '{widget}' does not take '{key}'",
                    [*loc, key],
                )
        return None

    if not multiple and entry.max_files is not None and entry.max_files > 1:
        state.error(
            FormIssueCode.UNSUPPORTED_WIDGET_OPTION,
            f"Input '{entry.field}' takes one file. Use a list[dict] input for more.",
            [*loc, "max_files"],
        )
    max_files = entry.max_files or MAX_FILES_PER_FIELD
    return FormFieldConfig(
        accept=entry.accept,
        max_files=max_files if multiple else 1,
        max_file_size=app_config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES,
    )


def _resolve_visible_if(
    state: _Resolution, entry: FormFieldEntry, loc: IssueLoc
) -> FormVisibleIf | None:
    rule = entry.visible_if
    if rule is None:
        return None

    rule_loc: IssueLoc = [*loc, "visible_if"]
    if not state.expects[entry.field].has_default():
        state.error(
            FormIssueCode.CONDITIONAL_FIELD_REQUIRED,
            f"'{entry.field}' is conditional, so the input needs a default",
            rule_loc,
        )
    if rule.field not in state.expects:
        state.error(
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
        or (kind not in SCALAR_KINDS)
    ):
        state.error(
            FormIssueCode.VISIBLE_IF_INVALID_CONTROLLER,
            f"'{rule.field}' must be an earlier, unconditional field of type "
            "str, int, float, bool or enum",
            [*rule_loc, "field"],
        )
        return rule

    if not _is_controller_value(rule.equals, kind, controller):
        state.error(
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
