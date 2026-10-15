"""The form config schema, the widget registry, the resolver and value checks.

All pure: no database.
"""

import base64
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from tracecat import config as app_config
from tracecat.expressions.schemas import ExpectedField
from tracecat.forms.constants import (
    MAX_ACCEPT_ENTRIES,
    MAX_CONFIG_IMAGE_BYTES,
    MAX_FIELD_OPTIONS,
    MAX_IMAGE_BYTES,
    MAX_LAYOUT_ENTRIES,
    MAX_LONG_TEXT_LENGTH,
    MAX_SHORT_TEXT_LENGTH,
)
from tracecat.forms.enums import (
    FormAutofillSource,
    FormFieldErrorCode,
    FormInputKind,
    FormIssueSeverity,
    FormWidget,
)
from tracecat.forms.enums import FormIssueCode as Code
from tracecat.forms.resolver import (
    DEFAULT_WIDGETS,
    WIDGETS,
    classify_input,
    resolve_form,
)
from tracecat.forms.schemas import (
    FormConfig,
    FormDividerEntry,
    FormFieldBlock,
    FormFieldEntry,
    FormHeadingEntry,
    FormNoticeEntry,
    FormTextEntry,
)
from tracecat.forms.types import ResolveResult
from tracecat.forms.values import check_values, visible_fields

PNG = b"\x89PNG\r\n\x1a\n"
STR, BOOL = {"type": "str"}, {"type": "bool"}
OPTIONAL_STR = {"type": "str", "default": ""}
MODEL = {"type": 'enum["mba", "mbp"]'}


def _image(subtype: str, data: bytes) -> str:
    return f"data:image/{subtype};base64,{base64.b64encode(data).decode()}"


def _with_images(*images: str) -> dict[str, Any]:
    options = [{"value": f"o{i}", "image": image} for i, image in enumerate(images)]
    return {"layout": [{"field": "model", "widget": "cards", "options": options}]}


def _resolve(layout: list[dict[str, Any]] | None, **fields: Any) -> ResolveResult:
    expects = {name: ExpectedField.model_validate(f) for name, f in fields.items()}
    return resolve_form(FormConfig.model_validate({"layout": layout}), expects)


def _fields(result: ResolveResult) -> dict[str, FormFieldBlock]:
    return {field.name: field for field in result.fields}


def _codes(result: ResolveResult) -> list[Code]:
    return [issue.code for issue in result.issues]


def _when(field: str, equals: Any) -> dict[str, Any]:
    return {"visible_if": {"field": field, "equals": equals}}


# ----- Config schema -----


@pytest.mark.parametrize(
    "image",
    [
        _image("png", PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG))),
        _image("jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 10),
        _image("webp", b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10),
    ],
)
def test_option_images_accept_png_jpeg_and_webp_up_to_the_limit(image: str) -> None:
    FormConfig.model_validate(_with_images(image))


@pytest.mark.parametrize(
    "image",
    [
        "https://example.com/laptop.png",
        "data:image/gif;base64,R0lGODlh",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png,not-base64-marked",
        "data:image/png;base64,not base64!",
        # Declared type and content disagree.
        _image("png", b"\xff\xd8\xff\xe0" + b"\x00" * 10),
        _image("webp", b"RIFF\x00\x00\x00\x00WAVE"),
        _image("jpeg", b""),
        _image("png", PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG) + 1)),
    ],
)
def test_option_images_reject_anything_else(image: str) -> None:
    with pytest.raises(ValidationError):
        FormConfig.model_validate(_with_images(image))


def test_an_option_value_cannot_be_empty() -> None:
    # An empty answer counts as unanswered, so this option could never be sent.
    layout = [{"field": "model", "options": [{"value": "", "label": "None"}]}]

    with pytest.raises(ValidationError) as exc_info:
        FormConfig.model_validate({"layout": layout})

    [error] = exc_info.value.errors()
    assert error["loc"][-3:] == ("options", 0, "value")


def test_images_of_one_config_are_limited_in_total() -> None:
    image = _image("png", PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG)))
    count = MAX_CONFIG_IMAGE_BYTES // MAX_IMAGE_BYTES

    FormConfig.model_validate(_with_images(*[image] * count))
    with pytest.raises(ValidationError, match="in total"):
        FormConfig.model_validate(_with_images(*[image] * (count + 1)))


def test_layout_entries_are_discriminated_by_their_key() -> None:
    layout = [
        {"notice": "Reviewed within 2 business days.", "tone": "warning"},
        {"field": "model"},
        {"heading": "Delivery"},
        {"text": "Read the **policy** first."},
        {"divider": True},
    ]

    config = FormConfig.model_validate({"layout": layout})

    assert [type(entry) for entry in config.layout or []] == [
        FormNoticeEntry,
        FormFieldEntry,
        FormHeadingEntry,
        FormTextEntry,
        FormDividerEntry,
    ]


SHORT = "x" * (MAX_SHORT_TEXT_LENGTH + 1)
LONG = "x" * (MAX_LONG_TEXT_LENGTH + 1)


def _entry(**keys: Any) -> dict[str, Any]:
    return {"layout": [{"field": "a", **keys}]}


@pytest.mark.parametrize(
    "config",
    [
        {"status": "online"},
        {"layout": [{}]},
        {"layout": [{"label": "No identifying key"}]},
        {"layout": [{"field": "model", "heading": "Two identifying keys"}]},
        {"layout": [{"divider": False}]},
        {"layout": [{"heading": "Delivery", "unknown": 1}]},
        _entry(widget="slider"),
        _entry(max_files=11),
        _entry(options=[]),
        _entry(options=[{"value": "a"}, {"value": "a"}]),
        _entry(autofill="requester.phone"),
        _entry(**_when("other", ["a"])),
        # A hidden or autofilled field shows nothing, so it sets nothing.
        _entry(hidden=True, label="Requester"),
        _entry(autofill="requester", widget="user"),
        _entry(hidden=True, **_when("other", True)),
        # Lengths and counts are bounded.
        {"title": SHORT},
        {"description": LONG},
        {"success_message": LONG},
        {"layout": [{"divider": True}] * (MAX_LAYOUT_ENTRIES + 1)},
        {"layout": [{"heading": SHORT}]},
        {"layout": [{"text": LONG}]},
        {"layout": [{"notice": LONG}]},
        {"layout": [{"field": SHORT}]},
        _entry(label=SHORT),
        _entry(help=LONG),
        _entry(placeholder=SHORT),
        _entry(options=[{"value": str(i)} for i in range(MAX_FIELD_OPTIONS + 1)]),
        _entry(options=[{"value": SHORT}]),
        _entry(options=[{"value": "v", "label": SHORT}]),
        _entry(options=[{"value": "v", "description": LONG}]),
        _entry(accept=[".pdf"] * (MAX_ACCEPT_ENTRIES + 1)),
        _entry(accept=[SHORT]),
        _entry(**_when("b", SHORT)),
    ],
)
def test_invalid_configs_are_rejected(config: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FormConfig.model_validate(config)


# ----- Widget registry -----

KIND_BY_TYPE = {
    "str": FormInputKind.STR,
    "str | None": FormInputKind.STR,
    "int": FormInputKind.INT,
    "float": FormInputKind.FLOAT,
    "bool": FormInputKind.BOOL,
    "datetime": FormInputKind.DATETIME,
    'enum["low", "high"]': FormInputKind.ENUM,
    'enum["low", "high"] | None': FormInputKind.ENUM,
    "list[str]": FormInputKind.LIST_STR,
    'list[enum["low", "high"]]': FormInputKind.LIST_ENUM,
    "dict": FormInputKind.DICT,
    "dict[str, Any]": FormInputKind.DICT,
    "list[dict]": FormInputKind.LIST_DICT,
    "any": FormInputKind.ANY,
    "Any": FormInputKind.ANY,
    "duration": FormInputKind.UNSUPPORTED,
    "None": FormInputKind.UNSUPPORTED,
    "list[int]": FormInputKind.UNSUPPORTED,
    "str | int": FormInputKind.UNSUPPORTED,
    "$Reference": FormInputKind.UNSUPPORTED,
    "not a type": FormInputKind.UNSUPPORTED,
}


@pytest.mark.parametrize(("type_string", "kind"), KIND_BY_TYPE.items())
def test_classify_input(type_string: str, kind: FormInputKind) -> None:
    classified = classify_input(type_string, "field")

    assert classified.kind is kind
    if kind in (FormInputKind.ENUM, FormInputKind.LIST_ENUM):
        assert classified.enum_values == ("low", "high")


@pytest.mark.parametrize("kind", FormInputKind)
def test_every_input_kind_gets_its_default_widget_or_none(kind: FormInputKind) -> None:
    type_string = next(t for t, k in KIND_BY_TYPE.items() if k is kind)
    default = DEFAULT_WIDGETS.get(kind)

    required = _resolve(None, value={"type": type_string})
    optional = _resolve(None, value={"type": type_string, "default": None})

    if default is None:
        assert _codes(required) == [Code.NO_WIDGET_FOR_TYPE]
        assert _codes(optional) == [Code.UNRENDERED_OPTIONAL_INPUT]
        assert optional.issues[0].severity is FormIssueSeverity.WARNING
        assert not required.blocks and not optional.blocks
    else:
        assert not required.issues
        assert _fields(required)["value"].widget is default
        assert _fields(optional)["value"].required is False


@pytest.mark.parametrize("widget", FormWidget)
@pytest.mark.parametrize("kind", FormInputKind)
def test_widget_compatibility_for_every_input_kind(
    widget: FormWidget, kind: FormInputKind
) -> None:
    type_string = next(t for t, k in KIND_BY_TYPE.items() if k is kind)
    entry: dict[str, Any] = {"field": "value", "widget": widget}
    if WIDGETS[widget].options:
        entry["options"] = [{"value": "low"}]

    result = _resolve([entry], value={"type": type_string})

    if kind in WIDGETS[widget].accepts:
        assert not result.has_errors
        assert _fields(result)["value"].widget is widget
    else:
        assert _codes(result) == [Code.INCOMPATIBLE_WIDGET]
        assert result.issues[0].loc == ["layout", 0, "widget"]
        assert not result.blocks


# ----- Resolver -----

EXPECTS: dict[str, dict[str, Any]] = {
    "model": MODEL | {"description": "Laptop model"},
    "justification": STR,
    "for_someone_else": BOOL | {"default": False},
    "recipient": {"type": "dict | None", "default": None},
    "quote": {"type": "list[dict]", "default": []},
    "needed_by": {"type": "str | None", "default": None},
    "requester": {"type": "dict | None"},
    "priority": {"type": "str", "default": "normal"},
    "size": STR,
    "notes": OPTIONAL_STR,
}
LAYOUT: list[dict[str, Any]] = [
    {"notice": "Requests are reviewed within 2 business days."},
    {
        "field": "model",
        "label": "Choose your laptop",
        "widget": "cards",
        "options": [{"value": "mbp", "label": "MacBook Pro 14"}],
    },
    {"field": "justification", "widget": "long_text"},
    {"heading": "Delivery"},
    {"field": "for_someone_else"},
    {"field": "recipient", "widget": "user", **_when("for_someone_else", True)},
    {"field": "quote", "widget": "file", "accept": [".pdf", "image/*"], "max_files": 2},
    {"field": "needed_by", "widget": "date"},
    {"field": "requester", "autofill": "requester"},
    {"field": "priority", "hidden": True},
    {"field": "size", "widget": "select", "options": [{"value": "s"}, {"value": "m"}]},
]
VALID: dict[str, Any] = {"model": "mbp", "justification": "Mine broke", "size": "s"}


def test_resolves_a_full_layout() -> None:
    result = _resolve(LAYOUT, **EXPECTS)

    assert not result.issues
    assert [block.type for block in result.blocks[:5]] == [
        "notice",
        "field",
        "field",
        "heading",
        "field",
    ]
    assert result.autofill == {"requester": FormAutofillSource.REQUESTER}
    assert result.hidden == ["priority"]
    fields = _fields(result)
    # Inputs the layout leaves out follow in schema order.
    assert list(fields) == [
        "model",
        "justification",
        "for_someone_else",
        "recipient",
        "quote",
        "needed_by",
        "size",
        "notes",
    ]
    model = fields["model"]
    assert (model.label, model.help, model.required, model.multiple) == (
        "Choose your laptop",
        "Laptop model",
        True,
        False,
    )
    # Decorated enum options come first, the rest of the enum follows.
    assert [(o.value, o.label) for o in model.options or []] == [
        ("mbp", "MacBook Pro 14"),
        ("mba", "mba"),
    ]
    # Options of a str input are the allowed values.
    assert [o.value for o in fields["size"].options or []] == ["s", "m"]
    toggle = fields["for_someone_else"]
    assert (toggle.label, toggle.widget, toggle.default) == (
        "For someone else",
        FormWidget.TOGGLE,
        False,
    )
    assert fields["recipient"].visible_if is not None
    assert fields["recipient"].required is False
    quote = fields["quote"]
    assert quote.multiple is True
    assert quote.config is not None
    assert (quote.config.accept, quote.config.max_files) == ([".pdf", "image/*"], 2)
    single = _fields(_resolve([{"field": "q", "widget": "file"}], q={"type": "dict"}))
    assert single["q"].config is not None
    assert single["q"].config.max_files == 1
    assert single["q"].config.max_file_size == (
        app_config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
    )


ISSUE_CASES: list[tuple[Code, list[dict[str, Any]], dict[str, Any], list]] = [
    (Code.UNKNOWN_FIELD, [{"field": "missing"}], {"a": STR}, [0, "field"]),
    (Code.DUPLICATE_FIELD, [{"field": "a"}] * 2, {"a": STR}, [1, "field"]),
    (
        Code.INCOMPATIBLE_WIDGET,
        [{"field": "a", "widget": "toggle"}],
        {"a": STR},
        [0, "widget"],
    ),
    (Code.NO_WIDGET_FOR_TYPE, [{"field": "a"}], {"a": {"type": "duration"}}, [0]),
    (
        Code.UNSUPPORTED_WIDGET_OPTION,
        [{"field": "a", "accept": [".pdf"]}],
        {"a": STR},
        [0, "accept"],
    ),
    (
        Code.UNSUPPORTED_WIDGET_OPTION,
        [{"field": "a", "placeholder": "Yes"}],
        {"a": BOOL},
        [0, "placeholder"],
    ),
    (
        Code.UNSUPPORTED_WIDGET_OPTION,
        [{"field": "a", "widget": "file", "max_files": 2}],
        {"a": {"type": "dict"}},
        [0, "max_files"],
    ),
    (Code.OPTIONS_REQUIRED, [{"field": "a", "widget": "select"}], {"a": STR}, [0]),
    (
        Code.OPTIONS_NOT_SUPPORTED,
        [{"field": "a", "options": [{"value": "x"}]}],
        {"a": STR},
        [0, "options"],
    ),
    (
        Code.OPTION_NOT_IN_ENUM,
        [{"field": "a", "options": [{"value": "mba"}, {"value": "dell"}]}],
        {"a": MODEL},
        [0, "options", 1, "value"],
    ),
    (
        Code.CONDITIONAL_FIELD_REQUIRED,
        [{"field": "a", "hidden": True}],
        {"a": STR},
        [0, "hidden"],
    ),
    (
        Code.CONDITIONAL_FIELD_REQUIRED,
        [{"field": "b"}, {"field": "a", **_when("b", True)}],
        {"b": BOOL, "a": STR},
        [1, "visible_if"],
    ),
    (
        Code.VISIBLE_IF_UNKNOWN_FIELD,
        [{"field": "a", **_when("missing", True)}],
        {"a": OPTIONAL_STR},
        [0, "visible_if", "field"],
    ),
    (
        # The controller comes later in the layout.
        Code.VISIBLE_IF_INVALID_CONTROLLER,
        [{"field": "a", **_when("b", True)}, {"field": "b"}],
        {"b": BOOL, "a": OPTIONAL_STR},
        [0, "visible_if", "field"],
    ),
    (
        # The controller is itself conditional.
        Code.VISIBLE_IF_INVALID_CONTROLLER,
        [
            {"field": "c"},
            {"field": "b", **_when("c", True)},
            {"field": "a", **_when("b", True)},
        ],
        {"c": BOOL, "b": BOOL | {"default": False}, "a": OPTIONAL_STR},
        [2, "visible_if", "field"],
    ),
    (
        # The controller is not a scalar.
        Code.VISIBLE_IF_INVALID_CONTROLLER,
        [{"field": "b"}, {"field": "a", **_when("b", "x")}],
        {"b": {"type": "list[str]"}, "a": OPTIONAL_STR},
        [1, "visible_if", "field"],
    ),
    (
        Code.VISIBLE_IF_INVALID_VALUE,
        [{"field": "b"}, {"field": "a", **_when("b", "yes")}],
        {"b": BOOL, "a": OPTIONAL_STR},
        [1, "visible_if", "equals"],
    ),
    (
        Code.VISIBLE_IF_INVALID_VALUE,
        [{"field": "b"}, {"field": "a", **_when("b", "dell")}],
        {"b": MODEL, "a": OPTIONAL_STR},
        [1, "visible_if", "equals"],
    ),
    (
        Code.AUTOFILL_TYPE_MISMATCH,
        [{"field": "a", "autofill": "requester"}],
        {"a": STR},
        [0, "autofill"],
    ),
    (
        Code.AUTOFILL_TYPE_MISMATCH,
        [{"field": "a", "autofill": "requester.email"}],
        {"a": {"type": "dict"}},
        [0, "autofill"],
    ),
    (
        # Hidden would win and the input would silently take its default.
        Code.HIDDEN_WITH_AUTOFILL,
        [{"field": "a", "hidden": True, "autofill": "requester.email"}],
        {"a": OPTIONAL_STR},
        [0, "autofill"],
    ),
    (
        # A hidden default is never an answer, but every int default is checked.
        Code.INTEGER_OUT_OF_RANGE,
        [{"field": "a", "hidden": True}],
        {"a": {"type": "int", "default": -(2**53)}},
        [0],
    ),
    (
        Code.INTEGER_OUT_OF_RANGE,
        [{"field": "b"}, {"field": "a", **_when("b", -(2**53))}],
        {"b": {"type": "int", "default": 0}, "a": OPTIONAL_STR},
        [1, "visible_if", "equals"],
    ),
]


@pytest.mark.parametrize(("code", "layout", "expects", "loc"), ISSUE_CASES)
def test_resolver_error_codes(
    code: Code,
    layout: list[dict[str, Any]],
    expects: dict[str, Any],
    loc: list[str | int],
) -> None:
    result = _resolve(layout, **expects)

    assert [(issue.code, issue.loc) for issue in result.issues] == [
        (code, ["layout", *loc])
    ]
    assert result.issues[0].severity is FormIssueSeverity.ERROR


def test_every_issue_code_is_covered() -> None:
    # The warning is covered by the default-widget test, and a field default by
    # the test below.
    covered = {case[0] for case in ISSUE_CASES} | {
        Code.UNRENDERED_OPTIONAL_INPUT,
        Code.DEFAULT_INVALID,
    }

    assert covered == set(Code)


SIZES = [{"value": "s"}, {"value": "m"}]
OPTION_WIDGETS = {"select", "multi_select", "cards"}


@pytest.mark.parametrize(
    ("widget", "type_string", "default", "valid"),
    [
        ("select", "str | None", "m", True),
        ("cards", "str | None", "m", True),
        ("select", "str | None", None, True),
        ("select", "str | None", "xl", False),
        ("cards", "str | None", "xl", False),
        # A list is not one option, even when its items are options.
        ("select", "str | None", ["m"], False),
        ("multi_select", "list[str]", ["s", "m"], True),
        ("multi_select", "list[str]", [], True),
        ("cards", "list[str]", ["m"], True),
        ("multi_select", "list[str]", ["s", "xl"], False),
        ("cards", "list[str]", ["xl"], False),
        ("multi_select", "list[str]", "m", False),
        ("date", "str", "2026-02-03", True),
        ("date", "str", "tomorrow", False),
        # A timestamp is not a date.
        ("date", "datetime", "2026-02-03T10:00:00", False),
        ("datetime", "datetime", "2026-02-03T10:00:00", True),
        ("datetime", "str", "tomorrow", False),
        ("datetime", "str", "2026-02-03", False),
        ("number", "int", 2**53 - 1, True),
        # A browser would round it, so the page would show and send another.
        ("number", "int", 2**53, False),
        ("number", "int", "abc", False),
        # A file field starts empty, and the server applies the default.
        ("file", "dict | None", {"id": "x"}, True),
    ],
)
def test_a_field_default_must_be_a_valid_answer(
    widget: str, type_string: str, default: Any, valid: bool
) -> None:
    # The page starts with the default as the answer, so a default the server
    # rejects would make an untouched form fail to submit.
    entry: dict[str, Any] = {"field": "a", "widget": widget}
    if widget in OPTION_WIDGETS:
        entry["options"] = SIZES
    result = _resolve([entry], a={"type": type_string, "default": default})

    expected = [] if valid else [(Code.DEFAULT_INVALID, ["layout", 0])]
    assert [(issue.code, issue.loc) for issue in result.issues] == expected


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("2026-02-03T10:00", True),
        ("2026-02-03T10:00:00Z", True),
        # What the widget sends.
        ("2026-02-03T10:00:00.000Z", True),
        ("2026-02-03T10:00:00.123456+08:00", True),
        # Python parses these; a browser shows Invalid Date.
        ("2026-W06-2T10:00:00", False),
        ("20260203T100000", False),
        ("2026-02-03T10", False),
        ("2026-02-03 10:00:00", False),
        ("2026-02-03T10:00:00+0800", False),
        # Shaped right, but not a real date or time.
        ("2026-02-30T10:00:00", False),
        ("2026-02-03T10:00:60", False),
        # Python reads this as +02:00; a browser rejects it.
        ("2026-02-03T10:00:00+01:60", False),
    ],
)
def test_a_datetime_answer_or_default_must_be_one_every_browser_parses(
    value: str, valid: bool
) -> None:
    """Datetime answers and defaults take only the ISO form ``new Date()`` reads."""
    answered = check_values(
        _resolve(None, at={"type": "datetime"}).blocks, {"at": value}
    )
    defaulted = _resolve(None, at={"type": "datetime", "default": value})

    assert [error.code for error in answered.errors] == (
        [] if valid else [FormFieldErrorCode.INVALID_DATE]
    )
    assert _codes(defaulted) == ([] if valid else [Code.DEFAULT_INVALID])


def test_a_default_issue_names_the_field_the_value_and_the_reason() -> None:
    result = _resolve(
        [{"field": "due", "widget": "date"}], due={"type": "str", "default": "soon"}
    )

    assert [issue.message for issue in result.issues] == [
        "The default of 'due', 'soon', is not a valid answer: Enter a date as "
        "YYYY-MM-DD"
    ]


@pytest.mark.parametrize(
    ("source", "type_string"),
    [
        (FormAutofillSource.REQUESTER, "dict"),
        (FormAutofillSource.REQUESTER, "any"),
        (FormAutofillSource.REQUESTER_EMAIL, "str"),
        (FormAutofillSource.REQUESTER_USER_ID, "str | None"),
        (FormAutofillSource.SUBMISSION_ID, "any"),
    ],
)
def test_autofill_fills_inputs_without_a_widget(
    source: FormAutofillSource, type_string: str
) -> None:
    result = _resolve([{"field": "a", "autofill": source}], a={"type": type_string})

    assert (result.issues, result.blocks) == ([], [])
    assert result.autofill == {"a": source}


# ----- Value checks -----


@pytest.mark.parametrize(
    ("values", "shown"),
    [
        ({}, False),
        ({"for_someone_else": False}, False),
        ({"for_someone_else": True}, True),
        # A truthy value of another type does not satisfy the rule.
        ({"for_someone_else": 1}, False),
        ({"for_someone_else": "true"}, False),
    ],
)
def test_conditional_field_follows_its_controller(
    values: dict[str, Any], shown: bool
) -> None:
    names = [
        field.name
        for field in visible_fields(_resolve(LAYOUT, **EXPECTS).blocks, values)
    ]

    assert ("recipient" in names) is shown


def test_missing_controller_counts_as_its_default() -> None:
    blocks = _resolve(
        [{"field": "b"}, {"field": "a", **_when("b", True)}],
        b=BOOL | {"default": True},
        a=OPTIONAL_STR,
    ).blocks

    assert [f.name for f in visible_fields(blocks, {})] == ["b", "a"]
    assert [f.name for f in visible_fields(blocks, {"b": False})] == ["b"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"size": "s"}, {"model": "required", "justification": "required"}),
        (
            VALID | {"model": "", "justification": None},
            {"model": "required", "justification": "required"},
        ),
        (VALID | {"model": "dell"}, {"model": "invalid_option"}),
        (VALID | {"model": ["mbp"]}, {"model": "invalid_value"}),
        (VALID | {"model": 3}, {"model": "invalid_option"}),
        (VALID | {"size": "xl"}, {"size": "invalid_option"}),
        (VALID | {"needed_by": "next week"}, {"needed_by": "invalid_date"}),
        (VALID | {"needed_by": "2026-02-30"}, {"needed_by": "invalid_date"}),
        (VALID | {"needed_by": "2026-02-03T10:00:00"}, {"needed_by": "invalid_date"}),
        # An ISO week date parses, but it is not YYYY-MM-DD.
        (VALID | {"needed_by": "2026-W06-2"}, {"needed_by": "invalid_date"}),
        (VALID | {"nope": 1}, {"nope": "unknown_field"}),
        # Hidden and autofilled inputs are not fields of the form.
        (VALID | {"priority": "urgent"}, {"priority": "unknown_field"}),
        (VALID | {"requester": {}}, {"requester": "unknown_field"}),
    ],
)
def test_check_values_errors(values: dict[str, Any], expected: dict[str, str]) -> None:
    checked = check_values(_resolve(LAYOUT, **EXPECTS).blocks, values)

    assert {error.field: error.code.value for error in checked.errors} == expected


def test_float_values_are_not_range_checked() -> None:
    # A float is a double in the browser too, so it is always held exactly.
    result = _resolve(
        [{"field": "ratio"}, {"field": "note", **_when("ratio", 2.0**60)}],
        ratio={"type": "float", "default": 2.0**53},
        scale={"type": "float", "default": 10**20},
        note=OPTIONAL_STR,
    )

    assert result.issues == []


def test_check_values_keeps_valid_answers_of_visible_fields() -> None:
    blocks = _resolve(
        [{"field": "tags", "widget": "multi_select", "options": [{"value": "a"}]}],
        tags={"type": "list[str]"},
        at={"type": "datetime"},
        agree=BOOL,
        notes=OPTIONAL_STR,
    ).blocks
    values = {"tags": ["a"], "at": "2026-02-03T10:00:00Z", "agree": False, "notes": ""}

    checked = check_values(blocks, values)

    assert checked.errors == []
    # False is an answer. An empty optional value is left to its default.
    assert checked.values == {k: v for k, v in values.items() if k != "notes"}
    for bad, code in [
        ({"tags": ["a", "b"]}, FormFieldErrorCode.INVALID_OPTION),
        ({"tags": "a"}, FormFieldErrorCode.INVALID_VALUE),
        ({"at": "soon"}, FormFieldErrorCode.INVALID_DATE),
        # A date alone is not a date and time.
        ({"at": "2026-02-03"}, FormFieldErrorCode.INVALID_DATE),
    ]:
        assert [e.code for e in check_values(blocks, values | bad).errors] == [code]
    # The value of a field the requester cannot see is dropped, not checked.
    laptop = _resolve(LAYOUT, **EXPECTS).blocks
    hidden = check_values(laptop, VALID | {"recipient": str(uuid.uuid4())})
    assert (hidden.errors, hidden.values) == ([], VALID)
